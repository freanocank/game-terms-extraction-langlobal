#!/usr/bin/env python3
import sys, os, time, signal, json, re, datetime
import asyncio
import hashlib
from io import BytesIO

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import pandas as pd
import yaml
import jieba
from openai import AsyncOpenAI

_interrupted = False


class PipelineInterruptedError(Exception):
    pass

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.languages import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    is_chinese,
    language_name,
    language_tag,
    validate_language_pair,
)

DEFAULT_TEMPLATE_FILENAME = "候选术语_模板.xlsx"
PROFILE_LABELS = {
    "yanyun": "燕云",
    "xiuxiu": "咻咻",
    "baoxiang": "宝箱",
    "gujian": "古剑",
}


@dataclass
class PipelineOpts:
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    target_language: str = DEFAULT_TARGET_LANGUAGE
    bilingual: bool = False
    no_translate: bool = False
    src_col: int = 0
    src_en_col: int = 1
    src_target_col: Optional[int] = None
    key_col: Optional[int] = None
    gl_cn_col: int = 0
    gl_en_col: int = 1
    gl_source_col: Optional[int] = None
    gl_target_col: Optional[int] = None
    embed_workers: int = 16
    max_concurrent: Optional[int] = None
    max_tokens: Optional[int] = None
    flag_unsourced: bool = True
    ner_use_main: Optional[bool] = None
    ner_api_key: Optional[str] = field(default=None, repr=False)
    ner_base_url: Optional[str] = None
    ner_model: Optional[str] = None
    embedding_use_main: Optional[bool] = None
    embedding_api_key: Optional[str] = field(default=None, repr=False)
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None

from config import EXTRACTOR_CONFIG, VOTE_TEMPS
from config_template import DEFAULT_API_BASE, DEFAULT_EMBEDDING_MODEL, DEFAULT_MODEL, DEFAULT_PROFILE
from core.prompt_base import build_system_prompt, build_user_prompt, build_translation_prompt
from core.llm_extractor import LLMExtractor
from core.llm_translator import TermTranslator
from core.logger import setup_logging
from core.checkpoint import (load as ckpt_load, save as ckpt_save,
                             save_meta as ckpt_save_meta, load_meta as ckpt_load_meta,
                             save_inputs as ckpt_save_inputs)
from core.embed_store import EmbedStore
from core.retry import retry_async
import logging
logger = logging.getLogger("pipeline")

TEMPLATE_COLUMNS = ["Key值", "术语分类", "术语原文", "术语译文", "匹配来源", "来源原文", "审核状态", "最新修订时间"]
MATCH_TYPE_SOURCE_LABELS = {
    "exact": "沿用术语表",
    "bilingual": "沿用原文英文",
    "aligned": "沿用同行译文",
    "llm_translated": "AI 翻译",
    "no_translate": "未生成译文",
}


def empty_glossary_bytes(
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> bytes:
    """Return a header-only glossary workbook accepted by the pipeline."""
    source_language, target_language = validate_language_pair(source_language, target_language)
    source_header = "中文术语" if source_language == DEFAULT_SOURCE_LANGUAGE else f"{language_name(source_language)}术语"
    target_header = "英文译文" if target_language == DEFAULT_TARGET_LANGUAGE else f"{language_name(target_language)}译文"
    output = BytesIO()
    pd.DataFrame(columns=[source_header, target_header]).to_excel(output, index=False)
    return output.getvalue()


def _safe_filename_part(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "", str(value or ""))
    value = re.sub(r"\s+", "", value)
    return value.strip("._- ")


def _source_descriptor(source_path: str = "") -> str:
    stem = Path(source_path).stem if source_path else ""
    if not stem:
        return ""
    tags = re.findall(r"【([^】]+)】", stem)
    plain = re.sub(r"【[^】]+】", " ", stem)
    plain = re.sub(r"\s+", " ", plain).strip()

    pieces = []
    version = next((t for t in tags if "版本" in t), "")
    env = next((t for t in tags if "global" in t.lower()), "")
    if version:
        pieces.append(version.replace("版本", ""))
    if env:
        pieces.append(env)
    if plain:
        pieces.append(plain.replace(" ", ""))
    return "_".join(filter(None, (_safe_filename_part(p) for p in pieces)))


def descriptive_template_filename(profile_name: str = "", source_path: str = "",
                                  run_date: str = "") -> str:
    """Return a PM-friendly annotation-template filename, or the legacy name without context."""
    label = PROFILE_LABELS.get(str(profile_name or "").strip(), _safe_filename_part(profile_name))
    descriptor = _source_descriptor(source_path)
    if not label and not descriptor:
        return DEFAULT_TEMPLATE_FILENAME
    date_part = _safe_filename_part(run_date or datetime.datetime.now().strftime("%Y%m%d"))
    parts = [p for p in [label, descriptor, "候选术语标注表", date_part] if p]
    return f"{'_'.join(parts)}.xlsx"


def results_to_template_df(results: List[dict], timestamp: str = "") -> pd.DataFrame:
    """Convert pipeline results to the unified 8-column annotation template."""
    ts = timestamp or datetime.datetime.now().isoformat(timespec="seconds")

    def _cell(r, k):
        v = r.get(k, "")
        return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)

    def _match_source(r):
        match_type = _cell(r, "match_type")
        return MATCH_TYPE_SOURCE_LABELS.get(match_type, match_type)

    rows = [{
        "Key值": _cell(r, "source_key"),
        "术语分类": _cell(r, "category"),
        "术语原文": _cell(r, "term"),
        "术语译文": _cell(r, "translation"),
        "匹配来源": _match_source(r),
        "来源原文": _cell(r, "source_text"),
        "审核状态": "未审核",
        "最新修订时间": ts,
    } for r in results]
    return pd.DataFrame(rows, columns=TEMPLATE_COLUMNS)


def dataframe_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


def results_workbook_bytes(results: List[dict]) -> bytes:
    return dataframe_to_xlsx_bytes(pd.DataFrame(results), "完整结果")


def template_workbook_bytes(results: List[dict]) -> bytes:
    return dataframe_to_xlsx_bytes(results_to_template_df(results), "术语表")


def save_outputs(results: List[dict], out_dir, profile_name: str = "",
                 source_path: str = "", run_date: str = "") -> Tuple[Path, Path]:
    """Write results.xlsx + annotation template files into out_dir, return primary paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res_path = out / "results.xlsx"
    tpl_path = out / descriptive_template_filename(profile_name, source_path, run_date)
    res_path.write_bytes(results_workbook_bytes(results))
    tpl_path.write_bytes(template_workbook_bytes(results))
    return res_path, tpl_path

_VAR_RE = re.compile(r'\$\{[^}]*\}|\$[a-zA-Z_]\w*|\$\d+|\{[^}]*\}|</?[a-zA-Z][^>]*>')


def _clean_src_text(s: str) -> str:
    """Strip variable placeholders ($var / {0} / <tag>) and surrounding whitespace."""
    return _VAR_RE.sub('', s).strip()


def _has_content(s: str) -> bool:
    """True if the text holds a word char (CJK/letter/digit); all-punctuation/symbol rows are False."""
    return bool(re.search(r'\w', s))


def _resolved_columns(opts: PipelineOpts) -> tuple[int, int, int]:
    target_text_col = opts.src_target_col if opts.src_target_col is not None else opts.src_en_col
    glossary_source_col = opts.gl_source_col if opts.gl_source_col is not None else opts.gl_cn_col
    glossary_target_col = opts.gl_target_col if opts.gl_target_col is not None else opts.gl_en_col
    return target_text_col, glossary_source_col, glossary_target_col


def prepare_source_texts(df_src: pd.DataFrame, opts: PipelineOpts) -> tuple[list[str], list[str]]:
    source_language, target_language = validate_language_pair(
        opts.source_language, opts.target_language
    )
    target_text_col, _, _ = _resolved_columns(opts)
    column_count = df_src.shape[1]
    if not 0 <= opts.src_col < column_count:
        raise ValueError("源文本列索引超出文件范围")
    if opts.bilingual and (
        not 0 <= target_text_col < column_count or target_text_col == opts.src_col
    ):
        raise ValueError("同行目标文本列索引无效或与源文本列重复")

    def _key_str(v):
        if pd.isna(v):
            return ""
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    keys_raw = (
        df_src.iloc[:, opts.key_col].map(_key_str)
        if opts.key_col is not None and 0 <= opts.key_col < column_count
        else None
    )
    key_list = keys_raw.tolist() if keys_raw is not None else None
    texts, text_keys, seen = [], [], set()
    if opts.bilingual:
        source_list = df_src.iloc[:, opts.src_col].fillna("").astype(str).tolist()
        target_list = df_src.iloc[:, target_text_col].fillna("").astype(str).tolist()
        source_tag = language_tag(source_language)
        target_tag = language_tag(target_language)
        for pos, (source_text, target_text) in enumerate(zip(source_list, target_list)):
            source_text = _clean_src_text(source_text)
            target_text = _clean_src_text(target_text)
            if not _has_content(source_text) or (source_text, target_text) in seen:
                continue
            seen.add((source_text, target_text))
            texts.append(f"{source_tag}: {source_text} | {target_tag}: {target_text}")
            text_keys.append(key_list[pos] if key_list is not None else "")
    else:
        for pos, value in enumerate(df_src.iloc[:, opts.src_col].tolist()):
            if pd.isna(value):
                continue
            source_text = _clean_src_text(str(value))
            if not _has_content(source_text) or source_text in seen:
                continue
            seen.add(source_text)
            texts.append(source_text)
            text_keys.append(key_list[pos] if key_list is not None else "")
    return texts, text_keys


def _on_interrupt(sig, frame):
    global _interrupted
    _interrupted = True
    logger.info("Interrupt received, finishing current batch...")


try:
    signal.signal(signal.SIGINT, _on_interrupt)
except ValueError:
    pass  # not in main thread (e.g. Streamlit background thread)


def load_config(profile_name: str = "yanyun") -> dict:
    with open(ROOT / "profiles" / f"{profile_name}.yaml", encoding="utf-8") as f:
        profile = yaml.safe_load(f)
    return profile


def load_glossary(path: str, cn_col: int = 0, en_col: int = 1) -> Tuple[dict, set]:
    df = pd.read_excel(path)
    glossary = {}
    keys = set()
    for _, row in df.iterrows():
        cn = str(row.iloc[cn_col]).strip()
        en = str(row.iloc[en_col]).strip()
        if cn and cn != "nan" and en and en != "nan":
            glossary[cn.lower()] = (cn, en)
            keys.add(cn)
    return glossary, keys


def _source_match_text(text: str, source_language: str, target_language: str) -> str:
    if (source_language, target_language) == (DEFAULT_SOURCE_LANGUAGE, DEFAULT_TARGET_LANGUAGE):
        return text
    source_prefix = f"{language_tag(source_language)}: "
    target_marker = f" | {language_tag(target_language)}: "
    if text.startswith(source_prefix) and target_marker in text:
        return text[len(source_prefix):].split(target_marker, 1)[0]
    return text


def _batch_match_context(
    terms: List[dict],
    source_texts: List[str],
    source_keys: List[str] = None,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> None:
    # whitespace-tolerant: source may hold internal spaces the model stripped (e.g. "武  茗" -> "武茗")
    _ws = re.compile(r"\s+")
    nterm = {t["term"]: _ws.sub("", t["term"]) for t in terms}
    remaining = {t["term"] for t in terms}
    term_context: Dict[str, str] = {}
    term_key: Dict[str, str] = {}
    for i, text in enumerate(source_texts):
        match_text = _source_match_text(text, source_language, target_language)
        ntext = _ws.sub("", match_text)
        found = [term for term in remaining if nterm[term] in ntext]
        for term in found:
            term_context[term] = text.replace("\n", " ").strip()
            if source_keys:
                term_key[term] = source_keys[i]
            remaining.discard(term)
        if not remaining:
            break
    for t in terms:
        t["source_text"] = term_context.get(t["term"], "")
        t["source_key"] = term_key.get(t["term"], "")


def _context_snapshot_for_checkpoint(terms: List[dict], source_texts: List[str],
                                     source_keys: List[str] = None,
                                     source_language: str = DEFAULT_SOURCE_LANGUAGE,
                                     target_language: str = DEFAULT_TARGET_LANGUAGE) -> List[dict]:
    snapshot = [dict(t) for t in terms]
    _batch_match_context(
        snapshot,
        source_texts,
        source_keys,
        source_language,
        target_language,
    )
    return snapshot


def _chunk_texts(texts: List[str], target_chars: int, overlap: int = 3) -> List[str]:
    chunks, cur, cur_c = [], [], 0
    for t in texts:
        if cur_c + len(t) > target_chars and cur:
            chunks.append("\n\n".join(cur))
            cur = cur[-overlap:]
            cur_c = sum(len(s) for s in cur) + (len(cur) - 1) * 2
        cur.append(t)
        cur_c += len(t)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _run_batch_votes(extractor, batch_c, system, batch_p, model, max_tokens, concurrent, batch_f, batch_id, progress_callback=None):
    async def _gather():
        async def _one(vote_idx, temp):
            results = await extractor.run_batch_async(
                texts=batch_c, system_prompt=system, user_prompts=batch_p,
                model=model, temperature=temp, max_tokens=max_tokens,
                max_concurrent=concurrent, source_files=batch_f,
                batch_id=f'{batch_id}_v{vote_idx}',
                progress=lambda done, total: progress_callback and progress_callback(
                    "extracting", 0, 0, f"v{vote_idx+1}: {done}/{total} chunks")
            )
            results.sort(key=lambda x: x.get("custom_id", ""))
            return results
        return await asyncio.gather(*[_one(v, VOTE_TEMPS[v]) for v in range(3)])
    return asyncio.run(_gather())


NER_SYSTEM = """从游戏中文文本中提取命名实体。严格按 JSON 格式输出:
{"persons": ["人名1", ...], "places": ["地名1", ...]}

规则:
- persons: 所有中文人名，包括 "老X"、"小X"、"阿X"、"X嫂"、"X伯"、"X叔"、"X爷"、"X娘"、"X儿"、"X哥"、"X弟"、"X师傅" 等称呼形式。不确定的标为 persons。
- places: 所有地名、地点、场所、门派、组织。
- 只输出 JSON，不要任何额外文字。"""


def _ner_system_prompt(source_language: str) -> str:
    if is_chinese(source_language):
        return NER_SYSTEM
    return f"""从{language_name(source_language)}游戏文本中提取命名实体。严格按 JSON 格式输出:
{{"persons": ["人名1", ...], "places": ["地名1", ...]}}

规则:
- persons: 文本中出现的所有角色名、人名和人物称谓。
- places: 文本中出现的所有地名、地点、场所、阵营和组织名。
- 如果条目包含带语言标签的同行译文，只分析 {language_tag(source_language)} 标签中的源文本，忽略其他语言字段。
- 保持实体在原文中的拼写，不要翻译。
- 只输出 JSON，不要任何额外文字。"""


def _rule_extract(batch_c, profile, source_language: str = DEFAULT_SOURCE_LANGUAGE):
    if not is_chinese(source_language):
        return []
    rules = profile.get("rule_extractors")
    if not rules:
        return []
    results = []
    if "surname_names" in rules:
        cfg = rules["surname_names"]
        surnames = set(cfg["surnames"])
        mn, mx = cfg.get("min_len", 2), cfg.get("max_len", 3)
        for c in batch_c:
            for token in jieba.cut(c):
                if mn <= len(token) <= mx and token[0] in surnames:
                    results.append(token)
    return list(set(results))


async def _ner_flash_batch(
    batch_c,
    api_key,
    base_url,
    model,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
):
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=1)
    sem = asyncio.Semaphore(5)
    results = {}
    ner_system = _ner_system_prompt(source_language)

    async def _one(idx, text):
        async with sem:
            async def _req():
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": ner_system}, {"role": "user", "content": text}],
                    temperature=0, max_tokens=2048,
                )
                raw = resp.choices[0].message.content.strip()
                m = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', raw)
                if m:
                    raw = m.group(1).strip()
                return json.loads(raw)
            try:
                parsed = await retry_async(_req, max_attempts=7, initial_delay=2.0,
                                           delay_cap=30, tag=f"NER c{idx}")
                results[idx] = (parsed.get("persons", []), parsed.get("places", []))
            except Exception:
                logger.warning(f"NER failed for chunk {idx} after 7 attempts")
                results[idx] = ([], [])

    tasks = [_one(i, c) for i, c in enumerate(batch_c) if c.strip()]
    for i, c in enumerate(batch_c):
        if not c.strip():
            results[i] = ([], [])
    await asyncio.gather(*tasks)

    all_persons, all_places = set(), set()
    for i in range(len(batch_c)):
        ps, pls = results.get(i, ([], []))
        all_persons.update(ps)
        all_places.update(pls)
    return list(all_persons), list(all_places)


def _ner_scan(
    batch_c,
    api_key,
    base_url,
    model,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
):
    return asyncio.run(_ner_flash_batch(batch_c, api_key, base_url, model, source_language))


def _collect_llm_votes(raw_v_all):
    all_votes = []
    for v, raw_v in enumerate(raw_v_all):
        for r in raw_v:
            for t in r.get("extracted_terms", {}).get("terms", []):
                term = (t.get("term") or t.get("source_term") or t.get("zh_term") or "").strip()
                if term:
                    target_term = (t.get("target_term") or t.get("eng_term") or "").strip()
                    all_votes.append({"term": term, "category": t.get("category", ""),
                                      "target_term": target_term, "_vote": v})
    return all_votes


def _pick_category(cat_counts: dict, allow: set) -> str:
    """多数表决分类：优先得票最高的合法分类(在 profile.term_categories 内)；全部超纲时退回模型多数票，保留术语不丢。"""
    if not cat_counts:
        return ""
    if allow:
        in_allow = {c: n for c, n in cat_counts.items() if c in allow}
        if in_allow:
            return max(in_allow.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return max(cat_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _count_llm_votes(all_votes, allowed_categories=None):
    allow = set(allowed_categories or [])
    term_counts, term_cats, term_disp, term_targets = {}, {}, {}, {}
    for t in all_votes:
        k = t["term"].lower()
        term_counts[k] = term_counts.get(k, 0) + 1
        cat = (t.get("category") or "").strip()
        if cat:
            c = term_cats.setdefault(k, {})
            c[cat] = c.get(cat, 0) + 1
        if k not in term_disp:
            term_disp[k] = t["term"]
        if not term_targets.get(k) and t.get("target_term"):
            term_targets[k] = t["target_term"]
    results = []
    for k, cnt in term_counts.items():
        if cnt >= 3:
            out = {"term": term_disp[k], "category": _pick_category(term_cats.get(k, {}), allow)}
            if term_targets.get(k):
                out["target_term"] = term_targets[k]
            results.append(out)
    return results


def _in_glossary(term: str, glossary_keys: set) -> bool:
    if not glossary_keys:
        return False
    folded = str(term or "").casefold()
    return any(str(key).casefold() == folded for key in glossary_keys)


def _glossary_hints(text: str, glossary_keys: set, source_language: str) -> list:
    if not glossary_keys:
        return []
    if is_chinese(source_language):
        return list(set(jieba.cut(text)) & glossary_keys)
    folded = text.casefold()
    return [key for key in glossary_keys if str(key).casefold() in folded]


def _filter_derived_terms(terms: List[dict], address_suffixes: list, glossary_keys: set = None) -> List[dict]:
    """Remove NPC 称谓派生变体 (e.g. 冯大哥→冯) keeping the canonical form.
    Terms in glossary_keys are never filtered.
    """
    suffixes = sorted(address_suffixes, key=len, reverse=True)
    term_set = set(t["term"] for t in terms)
    result = []
    for t in terms:
        term = t["term"]
        if _in_glossary(term, glossary_keys):
            result.append(t)
            continue
        is_derivative = False
        for suffix in suffixes:
            if term.endswith(suffix) and len(term) > len(suffix):
                stem = term[:-len(suffix)]
                if stem in term_set or any(o != term and o.startswith(stem) for o in term_set):
                    is_derivative = True
                    break
        if not is_derivative:
            result.append(t)
    return result


def extract_terms(texts: List[str], profile: dict, api_key: str, base_url: str, model: str,
                  target_chars: int = 1200,
                  output_dir: str = "", raw_dir: str = "", checkpoint_dir: str = "",
                  glossary_keys: set = None, opts: PipelineOpts = PipelineOpts(),
                  progress_callback: callable = None, text_keys: List[str] = None) -> List[dict]:
    source_language, target_language = validate_language_pair(
        opts.source_language, opts.target_language
    )
    concurrent = opts.max_concurrent or EXTRACTOR_CONFIG["max_concurrent"]
    max_tokens = opts.max_tokens or EXTRACTOR_CONFIG["max_tokens"]
    if not output_dir:
        output_dir = "output"
    t0 = time.time()
    chunks = _chunk_texts(texts, target_chars)

    start_idx = 0
    results = []
    last_completed = 0
    if checkpoint_dir:
        ckpt = ckpt_load(checkpoint_dir)
        if ckpt["chunk_idx"] > 0:
            start_idx = ckpt["chunk_idx"]
            last_completed = start_idx
            results = ckpt["terms"]
            logger.info(f"Checkpoint: resume from chunk {start_idx}/{len(chunks)}, {len(results)} terms")

    system = build_system_prompt(profile, source_language=source_language)
    extractor = LLMExtractor(api_key=api_key, base_url=base_url, base_dir=output_dir, cache_dir=checkpoint_dir)

    if raw_dir:
        os.makedirs(f"{raw_dir}/extraction", exist_ok=True)
        with open(f"{raw_dir}/extraction/system_prompt.txt", "w", encoding="utf-8") as f:
            f.write(system)

    for i in range(start_idx, len(chunks), concurrent):
        if _interrupted:
            logger.warning(f"Stopped at chunk {i}/{len(chunks)}")
            break

        end = min(i + concurrent, len(chunks))
        batch_c = chunks[i:end]
        if progress_callback:
            progress_callback("extracting", end, len(chunks),
                              f"NER 预扫描 chunk {i+1}-{end}…")
        ner_args = (
            batch_c,
            opts.ner_api_key or api_key,
            opts.ner_base_url or base_url,
            opts.ner_model or model,
        )
        if source_language == DEFAULT_SOURCE_LANGUAGE:
            ner_persons, ner_places = _ner_scan(*ner_args)
        else:
            ner_persons, ner_places = _ner_scan(*ner_args, source_language=source_language)
        if progress_callback:
            progress_callback("extracting", end, len(chunks),
                              f"NER done, starting LLM votes for chunk {i+1}-{end}…")
        rule_persons = _rule_extract(batch_c, profile, source_language)
        all_persons = list(set(ner_persons + rule_persons))
        batch_p = [
            build_user_prompt(
                profile,
                c,
                include_context=False,
                bilingual=opts.bilingual,
                jieba_hints=_glossary_hints(c, glossary_keys, source_language),
                ner_hints={'persons': [p for p in all_persons if p in c],
                           'places': [p for p in ner_places if p in c]},
                source_language=source_language,
                target_language=target_language,
            )
            for c in batch_c
        ]

        t_batch = time.time()
        pair_id = _safe_filename_part(f"{source_language}-{target_language}")
        raw_v_all = _run_batch_votes(extractor, batch_c, system, batch_p, model, max_tokens, concurrent,
                                     [f"c{j}" for j in range(i, end)], f'{pair_id}_b{i}',
                                     progress_callback=progress_callback)

        if raw_dir:
            for j, r in enumerate(raw_v_all[0]):
                with open(f"{raw_dir}/extraction/response_{i + j}.json", "w", encoding="utf-8") as f:
                    json.dump(r, f, ensure_ascii=False, indent=2, default=str)

        llm_votes = _collect_llm_votes(raw_v_all)
        batch_results = _count_llm_votes(llm_votes, profile.get("term_categories"))
        results.extend(batch_results)
        last_completed = end

        dt = time.time() - t_batch
        pct = end / len(chunks) * 100
        elapsed = time.time() - t0
        etc = elapsed / end * (len(chunks) - end) if end else 0
        logger.info(f"[{end}/{len(chunks)}] {pct:.0f}% | +{len(batch_results)} terms, total:{len(results)} | {dt:.0f}s | elapsed:{elapsed/60:.1f}m, ETC:{etc/60:.0f}m")
        if progress_callback:
            progress_callback("extracting", end, len(chunks),
                              f"+{len(batch_results)} terms, {len(results)} total | {elapsed/60:.1f}m elapsed, ETC {etc/60:.0f}m")

        if checkpoint_dir and (end % (concurrent * 2) == 0 or end == len(chunks)):
            ckpt_save(
                checkpoint_dir,
                end,
                _context_snapshot_for_checkpoint(
                    results, texts, text_keys, source_language, target_language
                ),
                len(chunks),
            )

    if _interrupted:
        if checkpoint_dir:
            ckpt_save(
                checkpoint_dir,
                last_completed,
                _context_snapshot_for_checkpoint(
                    results, texts, text_keys, source_language, target_language
                ),
                len(chunks),
            )
        raise PipelineInterruptedError("Extraction interrupted; checkpoint preserved")

    seen = {}
    for t in results:
        k = t["term"].lower()
        if k not in seen:
            seen[k] = t
    terms = list(seen.values())

    if is_chinese(source_language):
        address_suffixes = profile.get("address_suffixes", [])
        before = len(terms)
        terms = _filter_derived_terms(terms, address_suffixes, glossary_keys)
        if before != len(terms):
            logger.info(f"_filter_derived_terms removed {before - len(terms)} NPC nickname variants ({before} → {len(terms)})")

    _batch_match_context(
        terms,
        texts,
        text_keys,
        source_language,
        target_language,
    )

    return terms


def _aligned_target_value(
    term: dict,
    bilingual: bool,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> str:
    if not bilingual:
        return ""
    value = str(term.get("target_term") or term.get("eng_term") or "").strip()
    source_text = str(term.get("source_text") or "").strip()
    marker = f" | {language_tag(target_language)}: "
    if value and source_text and marker in source_text:
        target_text = source_text.split(marker, 1)[1]
        if value in target_text:
            return value
        exact_case = re.search(re.escape(value), target_text, flags=re.IGNORECASE)
        if exact_case:
            return exact_case.group(0)
        else:
            term["note"] = "; ".join(filter(None, [
                term.get("note", ""),
                f"同行{language_name(target_language)}译文未命中，已改为待生成",
            ]))
            return ""
    return value


def match_and_translate(extracted: List[dict], glossary: dict, profile: dict,
                        api_key: str, base_url: str, model: str,
                        raw_dir: str = "", embed_store: EmbedStore = None,
                        bilingual: bool = False,
                        source_language: str = DEFAULT_SOURCE_LANGUAGE,
                        target_language: str = DEFAULT_TARGET_LANGUAGE) -> List[dict]:
    source_language, target_language = validate_language_pair(source_language, target_language)
    legacy_pair = (
        source_language == DEFAULT_SOURCE_LANGUAGE
        and target_language == DEFAULT_TARGET_LANGUAGE
    )
    aligned_match_type = "bilingual" if legacy_pair else "aligned"

    def _clean(d):
        for k in ("_ref_term", "_ref_trans", "_ref_sim", "eng_term", "target_term"):
            d.pop(k, None)
        return d

    exact_matched, bilingual_copied, need_translate = [], [], []
    for t in extracted:
        aligned = _aligned_target_value(t, bilingual, target_language)
        hit = glossary.get(t["term"].lower())
        if hit:
            d = {**t, "translation": hit[1], "match_type": "exact"}
            if aligned and aligned != hit[1]:
                if legacy_pair:
                    d["note"] = f"EN列与术语表不一致，EN列原文：{aligned}"
                else:
                    d["note"] = (
                        f"{language_name(target_language)}列与术语表不一致，"
                        f"同行原文：{aligned}"
                    )
            exact_matched.append(d)
        elif aligned:
            bilingual_copied.append({**t, "translation": aligned, "match_type": aligned_match_type})
        else:
            need_translate.append(t)

    logger.info(f"Exact: {len(exact_matched)} | Bilingual copy: {len(bilingual_copied)} | Need LLM translate: {len(need_translate)}")

    if not need_translate:
        return [_clean(d) for d in exact_matched + bilingual_copied]

    logger.info("Computing top-1 reference via embedding...")
    query_terms = [t["term"] for t in need_translate]
    refs = embed_store.search(query_terms) if embed_store else []

    for i, t in enumerate(need_translate):
        cn_ref, sim = refs[i] if i < len(refs) else ("", 0.0)
        en_ref = glossary[cn_ref][1] if cn_ref and cn_ref in glossary else ""
        t["_ref_term"] = cn_ref
        t["_ref_trans"] = en_ref
        t["_ref_sim"] = sim

    TRANS_BATCH = 100
    batches = [need_translate[i:i + TRANS_BATCH] for i in range(0, len(need_translate), TRANS_BATCH)]
    prompts = [
        build_translation_prompt(
            profile,
            b,
            source_language=source_language,
            target_language=target_language,
        )
        for b in batches
    ]
    translator = TermTranslator(api_key=api_key, base_url=base_url, model=model, profile=profile)
    t_trans = time.time()
    logger.info(f"Translating {len(need_translate)} terms via LLM in {len(batches)} batches (size {TRANS_BATCH})...")
    trans_map = translator.translate_batches(prompts)
    dt_trans = time.time() - t_trans
    logger.info(f"Translation done: {len(trans_map)}/{len(need_translate)} mapped | {dt_trans:.0f}s")

    if raw_dir:
        os.makedirs(f"{raw_dir}/translation", exist_ok=True)
        with open(f"{raw_dir}/translation/prompts.txt", "w", encoding="utf-8") as f:
            for i, (sp, up) in enumerate(prompts):
                f.write(f"===== batch {i+1} =====\n[system]\n{sp}\n[user]\n{up}\n\n")
        with open(f"{raw_dir}/translation/response.json", "w", encoding="utf-8") as f:
            json.dump(list(trans_map.items()), f, ensure_ascii=False, indent=2)

    translated = []
    for t in need_translate:
        en = trans_map.get(t["term"], "")
        translated.append({**t, "translation": en, "match_type": "llm_translated",
                           "ref_term": t.get("_ref_term", ""), "ref_trans": t.get("_ref_trans", ""), "ref_sim": t.get("_ref_sim", 0)})

    return [_clean(d) for d in exact_matched + bilingual_copied + translated]


def run_pipeline(source_path: str, glossary_path: str, profile_name: str = DEFAULT_PROFILE,
                 api_key: str = "", base_url: str = DEFAULT_API_BASE,
                 model: str = DEFAULT_MODEL,
                 output_dir: str = "", raw_dir: str = "", checkpoint_dir: str = "",
                 progress_callback: callable = None,
                 opts: PipelineOpts = PipelineOpts()) -> List[dict]:

    global _interrupted
    _interrupted = False
    source_language, target_language = validate_language_pair(
        opts.source_language, opts.target_language
    )
    opts.source_language = source_language
    opts.target_language = target_language
    target_text_col, glossary_source_col, glossary_target_col = _resolved_columns(opts)

    if not output_dir:
        output_dir = "output"
    setup_logging(log_dir=output_dir)
    if checkpoint_dir:
        existing_meta = ckpt_load_meta(checkpoint_dir)
        existing_checkpoint = ckpt_load(checkpoint_dir)
        if existing_checkpoint.get("chunk_idx", 0) > 0:
            saved_source = existing_meta.get("source_language", DEFAULT_SOURCE_LANGUAGE)
            saved_target = existing_meta.get("target_language", DEFAULT_TARGET_LANGUAGE)
            if validate_language_pair(saved_source, saved_target) != (source_language, target_language):
                raise ValueError("断点的语言对与本次运行不一致，请选择对应断点或新建任务")
        ckpt_save_meta(checkpoint_dir, {
            "source_language": source_language, "target_language": target_language,
            "src_col": opts.src_col,
            "gl_cn_col": glossary_source_col, "gl_en_col": glossary_target_col,
            "gl_source_col": glossary_source_col, "gl_target_col": glossary_target_col,
            "bilingual": opts.bilingual,
            "src_en_col": target_text_col, "src_target_col": target_text_col,
            "no_translate": opts.no_translate,
            "key_col": opts.key_col,
            "profile": profile_name, "model": model,
            "ner_use_main": opts.ner_use_main,
            "ner_base_url": opts.ner_base_url or base_url,
            "ner_model": opts.ner_model or model,
            "embedding_use_main": opts.embedding_use_main,
            "embedding_base_url": opts.embedding_base_url or base_url,
            "embedding_model": opts.embedding_model or DEFAULT_EMBEDDING_MODEL,
            "src_filename": existing_meta.get("src_filename") or Path(source_path).name,
            "gl_filename": existing_meta.get("gl_filename") or Path(glossary_path).name,
            "glossary_hash": hashlib.md5(Path(glossary_path).read_bytes()).hexdigest()[:8],
        })
        source_path, glossary_path = ckpt_save_inputs(checkpoint_dir, source_path, glossary_path)
    profile = load_config(profile_name)
    glossary, glossary_keys = load_glossary(
        glossary_path,
        cn_col=glossary_source_col,
        en_col=glossary_target_col,
    )
    if is_chinese(source_language):
        for gk in glossary_keys:
            jieba.suggest_freq(gk, tune=True)

    embed_store = None
    if not opts.no_translate and glossary_keys:
        db_suffix = "" if source_language == DEFAULT_SOURCE_LANGUAGE else f"_{_safe_filename_part(source_language)}"
        db_path = ROOT / "database" / f"glossary_embeddings{db_suffix}.db"
        db_path.parent.mkdir(exist_ok=True)
        embed_store = EmbedStore(
            str(db_path),
            opts.embedding_api_key or api_key,
            opts.embedding_base_url or base_url,
            opts.embedding_model or DEFAULT_EMBEDDING_MODEL,
        )
        if progress_callback:
            progress_callback("loading", 0, 1, f"同步术语向量库 ({len(glossary_keys)} 条)…")
        added, removed = embed_store.sync(list(glossary_keys), progress=progress_callback, workers=opts.embed_workers)
        logger.info(f"Embedding store synced: +{added} added, -{removed} removed, {embed_store.count} total")
    elif not opts.no_translate:
        logger.info("No reference glossary supplied; skipping embedding-store sync")

    df_src = pd.read_excel(source_path)
    texts, text_keys = prepare_source_texts(df_src, opts)
    mode = "aligned" if opts.bilingual else "monolingual"
    logger.info(
        f"Source ({source_language}->{target_language}, {mode}): "
        f"{len(df_src)} rows -> {len(texts)} texts (dropped empty/punct/dup)"
    )
    if progress_callback:
        progress_callback("loading", 1, 1, f"{len(texts)} texts loaded, {len(glossary_keys)} glossary terms")
        progress_callback("extracting", 0, 1, "即将开始提取…")
    logger.info("Extracting terms...")
    extracted = extract_terms(texts, profile, api_key, base_url, model, output_dir=output_dir, raw_dir=raw_dir, checkpoint_dir=checkpoint_dir, glossary_keys=glossary_keys, opts=opts, progress_callback=progress_callback, text_keys=text_keys)

    filterable = set(profile.get("filterable_categories", []))
    if filterable:
        before = len(extracted)
        extracted = [t for t in extracted if t.get("category") not in filterable or _in_glossary(t["term"], glossary_keys)]
        if before != len(extracted):
            logger.info(f"category filter removed {before - len(extracted)} terms ({before} → {len(extracted)})")

    if opts.flag_unsourced:
        # source-not-found may be a real term (matching artifact) OR a hallucination — flag, never drop
        flagged = [t for t in extracted if not (t.get("source_text") or "").strip()]
        for t in flagged:
            t["note"] = "; ".join(filter(None, [t.get("note", ""), "⚠原文未命中,待核"]))
        if flagged:
            logger.info(f"flagged {len(flagged)} unsourced term(s) for review (not dropped): "
                        + "、".join(t["term"] for t in flagged))

    if opts.no_translate:
        def _clean_no_translate(t):
            aligned = _aligned_target_value(t, opts.bilingual, target_language)
            t.pop("target_term", None)
            t.pop("eng_term", None)
            match_type = (
                "bilingual"
                if aligned and source_language == DEFAULT_SOURCE_LANGUAGE and target_language == DEFAULT_TARGET_LANGUAGE
                else "aligned" if aligned else "no_translate"
            )
            return {**t, "translation": aligned, "match_type": match_type}
        results = [_clean_no_translate(t) for t in extracted]
        copied = sum(1 for r in results if r["match_type"] in {"bilingual", "aligned"})
        logger.info(f"No-translate mode: {len(results)} terms, {copied} with aligned target copied")
    else:
        logger.info("Matching + translating via embedding...")
        if progress_callback:
            progress_callback("translating", 0, 1, f"Exact: {sum(1 for t in extracted if t['term'].lower() in glossary)} matched, translating rest...")
        results = match_and_translate(
            extracted,
            glossary,
            profile,
            api_key,
            base_url,
            model,
            raw_dir=raw_dir,
            embed_store=embed_store,
            bilingual=opts.bilingual,
            source_language=source_language,
            target_language=target_language,
        )
        if progress_callback:
            progress_callback("translating", 1, 1, f"{len(results)} terms translated")

    for result in results:
        result.setdefault("source_language", source_language)
        result.setdefault("target_language", target_language)
    return results
