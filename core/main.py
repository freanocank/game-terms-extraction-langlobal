#!/usr/bin/env python3
import sys, os, time, signal, json, re, datetime
import asyncio
import hashlib
from io import BytesIO

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import pandas as pd
import yaml
import jieba

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
from core.source_records import (
    aligned_target_value as _aligned_target_value,
    batch_match_context as _batch_match_context,
    resolved_columns as _resolved_columns,
    prepare_source_records, prepare_source_texts, unique_records,
    records_from_texts, chunk_records, bind_context,
)
from core.voting import (
    collect_llm_votes as _collect_llm_votes,
    count_llm_votes as _count_llm_votes,
    ensure_complete, consolidate_terms,
)
from core.ner import ner_scan as _ner_scan, ner_system_prompt as _ner_system_prompt
from core.responses import PipelineIncompleteError, ResponseValidationError
from core.request_cache import fingerprint, write_json
from core.run_manifest import extraction_manifest, validate_resume

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




def _run_batch_votes(extractor, batch_c, system, batch_p, model, max_tokens, concurrent, batch_f, batch_id, progress_callback=None, records=None):
    async def _gather():
        semaphore = asyncio.Semaphore(concurrent)
        async def _one(vote_idx, temp):
            results = await extractor.run_batch_async(
                texts=batch_c, system_prompt=system, user_prompts=batch_p,
                model=model, temperature=temp, max_tokens=max_tokens,
                max_concurrent=concurrent, source_files=batch_f,
                batch_id=f'{batch_id}_v{vote_idx}',
                semaphore=semaphore,
                records=records,
                progress=lambda done, total: progress_callback and progress_callback(
                    "extracting", 0, 0, f"v{vote_idx+1}: {done}/{total} chunks")
            )
            results.sort(key=lambda x: x.get("custom_id", ""))
            return results
        tasks = [asyncio.create_task(_one(v, VOTE_TEMPS[v])) for v in range(3)]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    return extractor.run_sync(_gather())




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




def _in_glossary(term: str, glossary_keys: set) -> bool:
    if not glossary_keys:
        return False
    folded = str(term or "").casefold()
    return any(str(key).casefold() == folded for key in glossary_keys)


def _glossary_hints(text: str, glossary_keys: set, source_language: str) -> list:
    if not glossary_keys:
        return []
    if is_chinese(source_language):
        return sorted(set(jieba.cut(text)) & glossary_keys)
    folded = text.casefold()
    return sorted(key for key in glossary_keys if str(key).casefold() in folded)


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
                  progress_callback: callable = None, text_keys: List[str] = None,
                  source_records=None) -> List[dict]:
    source_language, target_language = validate_language_pair(opts.source_language, opts.target_language)
    concurrent = opts.max_concurrent if opts.max_concurrent is not None else EXTRACTOR_CONFIG["max_concurrent"]
    max_tokens = opts.max_tokens if opts.max_tokens is not None else EXTRACTOR_CONFIG["max_tokens"]
    if concurrent < 1 or max_tokens < 1 or target_chars < 1:
        raise ValueError("并发数、Token 上限和分块大小必须大于 0")
    records = source_records if source_records is not None else records_from_texts(texts, text_keys, source_language, target_language)
    record_chunks = chunk_records(records, target_chars)
    chunks = ["\n\n".join(f"[{r.row_id}] {r.text}" for r in chunk) for chunk in record_chunks]
    system = build_system_prompt(profile, source_language=source_language)
    identity = fingerprint({
        "version": 2, "chunks": chunks, "keys": [(r.row_id, r.key) for r in records],
        "profile": profile, "system": system, "language_pair": [source_language, target_language],
        "bilingual": opts.bilingual, "glossary": sorted(glossary_keys or []),
        "base_url": base_url.rstrip("/"), "model": model, "max_tokens": max_tokens,
        "ner_base": (opts.ner_base_url or base_url).rstrip("/"), "ner_model": opts.ner_model or model,
        "vote_temperatures": VOTE_TEMPS,
    })
    checkpoint = ckpt_load(checkpoint_dir) if checkpoint_dir else {}
    start_idx = checkpoint.get("chunk_idx", 0)
    if type(start_idx) is not int or not 0 <= start_idx <= len(chunks):
        raise ValueError("断点进度超出源文件范围，不能标为完成；旧断点已保留")
    if start_idx and (checkpoint.get("extraction_fingerprint") != identity or "votes" not in checkpoint):
        raise ValueError("提取断点与当前输入、规则或模型不兼容，请使用新的断点目录；旧断点未改动")
    votes = checkpoint.get("votes", [])
    completed_responses = checkpoint.get("responses", [[], [], []])
    results = _count_llm_votes(votes, profile.get("term_categories"))
    bind_context(results, records)

    def save_state(done, status="running", failures=None):
        if checkpoint_dir:
            ckpt_save(checkpoint_dir, done, results, len(chunks),
                      extraction_fingerprint=identity, votes=votes, responses=completed_responses,
                      status=status, failures=failures or [])

    def save_raw_responses(raw_votes, files=None):
        if raw_dir:
            for vote_id, responses in enumerate(raw_votes):
                for index, response in enumerate(responses):
                    source_file = response.get("source_file") or files[index]
                    chunk_index = int(source_file.removeprefix("c"))
                    write_json(Path(raw_dir) / "extraction" / f"b{chunk_index}_v{vote_id}_r0.json", response)

    extractor = LLMExtractor(api_key=api_key, base_url=base_url, base_dir=output_dir or "output", cache_dir=checkpoint_dir)
    t0 = time.time()
    try:
        save_state(start_idx)
        if raw_dir:
            write_json(Path(raw_dir) / "extraction" / "manifest.json", {
                "version": 2, "chunks": chunks, "profile": profile,
                "source_language": source_language, "target_language": target_language,
                "bilingual": opts.bilingual,
                "keys": [[r.row_id, r.key] for r in records],
                "glossary_keys": sorted(glossary_keys or []),
            })
            save_raw_responses(completed_responses)
        for i in range(start_idx, len(chunks), concurrent):
            if _interrupted:
                save_state(i, "interrupted")
                raise PipelineInterruptedError("Extraction interrupted; checkpoint preserved")
            end = min(i + concurrent, len(chunks))
            batch_c = chunks[i:end]
            if progress_callback:
                progress_callback("extracting", i, len(chunks), f"NER 预扫描 chunk {i + 1}-{end}…")
            ner_persons, ner_places = _ner_scan(
                batch_c, opts.ner_api_key or api_key, opts.ner_base_url or base_url, opts.ner_model or model,
                source_language=source_language, concurrent=concurrent,
            )
            all_persons = sorted(set(ner_persons + _rule_extract(batch_c, profile, source_language)))
            batch_p = [build_user_prompt(
                profile, chunk, include_context=False, bilingual=opts.bilingual,
                jieba_hints=_glossary_hints(chunk, glossary_keys, source_language),
                ner_hints={"persons": [p for p in all_persons if p in chunk],
                           "places": [p for p in ner_places if p in chunk]},
                source_language=source_language, target_language=target_language,
            ) for chunk in batch_c]
            files = [f"c{j}" for j in range(i, end)]
            raw_votes = _run_batch_votes(
                extractor, batch_c, system, batch_p, model, max_tokens, concurrent, files,
                f"{source_language}-{target_language}_b{i}", progress_callback=progress_callback, records=record_chunks[i:end],
            )
            save_raw_responses(raw_votes, files)
            try:
                ensure_complete(raw_votes, len(batch_c))
                batch_votes = _collect_llm_votes(raw_votes, dict(zip(files, record_chunks[i:end])))
            except (PipelineIncompleteError, ResponseValidationError) as exc:
                save_state(i, "incomplete", [str(exc)])
                if isinstance(exc, PipelineIncompleteError):
                    raise
                raise PipelineIncompleteError("来源校验", [str(exc)]) from exc
            votes.extend(batch_votes)
            for vote_id, responses in enumerate(raw_votes):
                completed_responses[vote_id].extend({**response, "source_file": response.get("source_file") or files[index]}
                                                     for index, response in enumerate(responses))
            results = _count_llm_votes(votes, profile.get("term_categories"))
            bind_context(results, records)
            save_state(end, "extracted" if end == len(chunks) else "running")
            logger.info("[%s/%s] %s row-grounded terms, %.1fs elapsed", end, len(chunks), len(results), time.time() - t0)
            if progress_callback:
                progress_callback("extracting", end, len(chunks), f"{len(results)} 条有投票支持的候选")
        if _interrupted:
            save_state(len(chunks), "interrupted")
            raise PipelineInterruptedError("Extraction interrupted; checkpoint preserved")
        save_state(len(chunks), "extracted")
    finally:
        extractor.close()

    terms = consolidate_terms(results)
    if is_chinese(source_language):
        terms = _filter_derived_terms(terms, profile.get("address_suffixes", []), glossary_keys)
    return terms




def match_and_translate(extracted: List[dict], glossary: dict, profile: dict,
                        api_key: str, base_url: str, model: str,
                        raw_dir: str = "", embed_store: EmbedStore = None,
                        bilingual: bool = False,
                        source_language: str = DEFAULT_SOURCE_LANGUAGE,
                        target_language: str = DEFAULT_TARGET_LANGUAGE,
                        translate_missing: bool = True,
                        checkpoint_dir: str = "") -> List[dict]:
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
                    conflict_note = f"EN列与术语表不一致，EN列原文：{aligned}"
                else:
                    conflict_note = (
                        f"{language_name(target_language)}列与术语表不一致，"
                        f"同行原文：{aligned}"
                    )
                d["note"] = "; ".join(filter(None, [t.get("note", ""), conflict_note]))
            exact_matched.append(d)
        elif aligned:
            bilingual_copied.append({**t, "translation": aligned, "match_type": aligned_match_type})
        else:
            need_translate.append(t)

    logger.info(
        f"Exact: {len(exact_matched)} | Aligned copy: {len(bilingual_copied)} | "
        f"Unresolved: {len(need_translate)}"
    )

    if not need_translate:
        return [_clean(d) for d in exact_matched + bilingual_copied]

    if not translate_missing:
        untranslated = [
            {**t, "translation": "", "match_type": "no_translate"}
            for t in need_translate
        ]
        logger.info(f"AI translation disabled: {len(untranslated)} unresolved terms left blank")
        return [_clean(d) for d in exact_matched + bilingual_copied + untranslated]

    logger.info("Computing top-1 reference via embedding...")
    query_terms = [t["term"] for t in need_translate]
    refs = embed_store.search(query_terms) if embed_store else []

    for i, t in enumerate(need_translate):
        cn_ref, sim = refs[i] if i < len(refs) else ("", 0.0)
        en_ref = glossary.get(cn_ref.lower(), ("", ""))[1] if cn_ref else ""
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
    from core.request_cache import RequestCache
    cache = RequestCache(checkpoint_dir, "translations")
    cache_key = fingerprint({"schema": 2, "provider": base_url.rstrip("/"), "model": model, "prompts": prompts})
    saved = cache.load(cache_key)
    trans_map = {key: value for key, value in (saved or {}).items()
                 if isinstance(value, str) and value.strip()} if isinstance(saved, dict) else {}
    pending = [term for term in need_translate if term["term"] not in trans_map]
    for attempt in range(2):
        if not pending:
            break
        pending_batches = [pending[i:i + TRANS_BATCH] for i in range(0, len(pending), TRANS_BATCH)]
        pending_prompts = [build_translation_prompt(
            profile, batch, source_language=source_language, target_language=target_language,
        ) for batch in pending_batches]
        received = translator.translate_batches(pending_prompts)
        for term in pending:
            value = received.get(term["term"])
            if isinstance(value, str) and value.strip():
                trans_map[term["term"]] = value.strip()
        cache.save(cache_key, trans_map)
        pending = [term for term in pending if term["term"] not in trans_map]
        logger.info("Translation attempt %s: %s mapped, %s pending", attempt + 1, len(trans_map), len(pending))
    if pending:
        raise PipelineIncompleteError("翻译", [term["term"] for term in pending])
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
    setup_logging(log_dir=output_dir, secrets=(api_key, opts.ner_api_key, opts.embedding_api_key))
    source_filename = Path(source_path).name
    profile = load_config(profile_name)
    glossary, glossary_keys = load_glossary(
        glossary_path, cn_col=glossary_source_col, en_col=glossary_target_col,
    )
    if checkpoint_dir:
        existing_meta = ckpt_load_meta(checkpoint_dir)
        source_filename = existing_meta.get("src_filename") or source_filename
        existing_checkpoint = ckpt_load(checkpoint_dir)
        manifest = extraction_manifest(source_path, glossary, profile, opts, base_url, model)
        validate_resume(existing_meta, existing_checkpoint, manifest)
        ckpt_save_meta(checkpoint_dir, {
            "extraction_manifest": manifest,
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
            "src_filename": source_filename,
            "gl_filename": existing_meta.get("gl_filename") or Path(glossary_path).name,
            "glossary_hash": hashlib.md5(Path(glossary_path).read_bytes()).hexdigest()[:8],
        })
        source_path, glossary_path = ckpt_save_inputs(checkpoint_dir, source_path, glossary_path)
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

    with pd.ExcelFile(source_path) as workbook:
        sheet = workbook.sheet_names[0]
        df_src = pd.read_excel(workbook, sheet_name=sheet)
    records = prepare_source_records(df_src, opts, source_filename, sheet)
    unique = unique_records(records)
    texts, text_keys = [r.text for r in unique], [r.key for r in unique]
    mode = "aligned" if opts.bilingual else "monolingual"
    logger.info(
        f"Source ({source_language}->{target_language}, {mode}): "
        f"{len(df_src)} rows -> {len(texts)} texts (dropped empty/punct/dup)"
    )
    if progress_callback:
        progress_callback("loading", 1, 1, f"{len(texts)} texts loaded, {len(glossary_keys)} glossary terms")
        progress_callback("extracting", 0, 1, "即将开始提取…")
    logger.info("Extracting terms...")
    extracted = extract_terms(texts, profile, api_key, base_url, model, output_dir=output_dir, raw_dir=raw_dir, checkpoint_dir=checkpoint_dir, glossary_keys=glossary_keys, opts=opts, progress_callback=progress_callback, text_keys=text_keys, source_records=records)

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

    logger.info("Resolving translations from glossary, aligned rows, and optional AI fallback...")
    if progress_callback:
        action = "整理现有译文" if opts.no_translate else "整理现有译文并生成其余译文"
        progress_callback("translating", 0, 1, action + "…")
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
        translate_missing=not opts.no_translate,
        checkpoint_dir=checkpoint_dir,
    )
    if progress_callback:
        translated = sum(1 for r in results if r["match_type"] == "llm_translated")
        blank = sum(1 for r in results if r["match_type"] == "no_translate")
        detail = f"{len(results)} 条术语已整理"
        if translated:
            detail += f"，AI 生成 {translated} 条译文"
        if blank:
            detail += f"，{blank} 条译文留空"
        progress_callback("translating", 1, 1, detail)

    for result in results:
        result.setdefault("source_language", source_language)
        result.setdefault("target_language", target_language)
    return results
