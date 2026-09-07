"""UI adapter layer — wraps pipeline with progress, config, and model management."""

from __future__ import annotations

import os
import json
import hashlib
import shutil
import sqlite3
import threading
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import List, Callable
import logging

from config_template import (
    DEFAULT_API_BASE,
    DEFAULT_CONCURRENT,
    DEFAULT_DETECT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBED_WORKERS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_NER_MODEL,
    DEFAULT_PROFILE,
)
from core.checkpoint import load as ckpt_load, load_meta as load_ckpt_meta, checkpoint_dir_name
from core.languages import DEFAULT_SOURCE_LANGUAGE, DEFAULT_TARGET_LANGUAGE, validate_language_pair

ROOT = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════

@dataclass
class RunConfig:
    api_key: str = field(default="", repr=False)
    api_base: str = DEFAULT_API_BASE
    model: str = DEFAULT_MODEL
    ner_use_main: bool = True
    ner_api_key: str = field(default="", repr=False)
    ner_api_base: str = DEFAULT_API_BASE
    ner_model: str = DEFAULT_NER_MODEL
    embedding_use_main: bool = True
    embedding_api_key: str = field(default="", repr=False)
    embedding_api_base: str = DEFAULT_API_BASE
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    detect_use_main: bool = True
    detect_api_key: str = field(default="", repr=False)
    detect_api_base: str = DEFAULT_API_BASE
    detect_model: str = DEFAULT_DETECT_MODEL
    profile: str = DEFAULT_PROFILE
    max_concurrent: int = DEFAULT_CONCURRENT
    embed_workers: int = DEFAULT_EMBED_WORKERS
    max_tokens: int = DEFAULT_MAX_TOKENS
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    target_language: str = DEFAULT_TARGET_LANGUAGE
    bilingual: bool = False
    no_translate: bool = False
    flag_unsourced: bool = True


@dataclass(frozen=True)
class ServiceSettings:
    api_key: str = field(default="", repr=False)
    api_base: str = ""
    model: str = ""


SERVICE_LABELS = {
    "main": "主生成服务",
    "ner": "人名/地名预识别（NER）服务",
    "embedding": "术语相似度匹配（Embedding）服务",
    "detect": "表头识别服务",
}


PERSISTED_CONFIG_FIELDS = (
    "api_base", "model", "profile", "source_language", "target_language",
    "ner_use_main", "ner_api_base", "ner_model",
    "embedding_use_main", "embedding_api_base", "embedding_model",
    "detect_use_main", "detect_api_base", "detect_model",
)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def default_config() -> RunConfig:
    ner_key = _first_env("NER_API_KEY")
    ner_base = _first_env("NER_API_BASE", "NER_BASE_URL")
    embedding_key = _first_env("EMBEDDING_API_KEY", "EMBED_API_KEY")
    embedding_base = _first_env(
        "EMBEDDING_API_BASE", "EMBEDDING_BASE_URL", "EMBED_API_BASE", "EMBED_BASE_URL"
    )
    detect_key = _first_env("DETECT_API_KEY")
    detect_base = _first_env("DETECT_API_BASE", "DETECT_BASE_URL")
    cfg = RunConfig(
        api_key=_first_env("OPENAI_API_KEY"),
        api_base=_first_env("OPENAI_API_BASE", "OPENAI_BASE_URL") or DEFAULT_API_BASE,
        model=_first_env("OPENAI_MODEL") or DEFAULT_MODEL,
        ner_use_main=not bool(ner_key or ner_base),
        ner_api_key=ner_key,
        ner_api_base=ner_base or DEFAULT_API_BASE,
        ner_model=_first_env("NER_MODEL") or DEFAULT_NER_MODEL,
        embedding_use_main=not bool(embedding_key or embedding_base),
        embedding_api_key=embedding_key,
        embedding_api_base=embedding_base or DEFAULT_API_BASE,
        embedding_model=_first_env("EMBEDDING_MODEL", "EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL,
        detect_use_main=not bool(detect_key or detect_base),
        detect_api_key=detect_key,
        detect_api_base=detect_base or DEFAULT_API_BASE,
        detect_model=_first_env("DETECT_MODEL") or DEFAULT_DETECT_MODEL,
    )
    _load_persisted_config(cfg)
    try:
        cfg.source_language, cfg.target_language = validate_language_pair(
            cfg.source_language, cfg.target_language
        )
    except ValueError:
        cfg.source_language = DEFAULT_SOURCE_LANGUAGE
        cfg.target_language = DEFAULT_TARGET_LANGUAGE
    return cfg


def _load_persisted_config(cfg: RunConfig):
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for name in PERSISTED_CONFIG_FIELDS:
                if name in data and data[name] not in (None, ""):
                    setattr(cfg, name, data[name])
            if any("key" in name.lower() or "token" in name.lower() or "secret" in name.lower()
                   for name in data):
                save_persisted_config(cfg)
        except Exception:
            pass


def check_checkpoint(
    file_bytes: bytes,
    profile: str,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> dict:
    source_language, target_language = validate_language_pair(source_language, target_language)
    h = hashlib.md5(file_bytes).hexdigest()[:8]
    ckpt_root = ROOT / "output" / "_checkpoints"
    if not ckpt_root.exists():
        return {"exists": False, "chunk_idx": 0, "total_chunks": 0, "terms": 0}
    for d in ckpt_root.iterdir():
        if d.is_dir() and h in d.name and profile in d.name:
            meta = load_ckpt_meta(str(d))
            saved_pair = (
                meta.get("source_language", DEFAULT_SOURCE_LANGUAGE),
                meta.get("target_language", DEFAULT_TARGET_LANGUAGE),
            )
            try:
                saved_pair = validate_language_pair(*saved_pair)
            except ValueError:
                continue
            if saved_pair != (source_language, target_language):
                continue
            ckpt = ckpt_load(str(d))
            if ckpt.get("chunk_idx", 0) > 0:
                return {"exists": True, "chunk_idx": ckpt["chunk_idx"],
                        "total_chunks": ckpt.get("total_chunks", 0),
                        "terms": len(ckpt.get("terms", []))}
    return {"exists": False, "chunk_idx": 0, "total_chunks": 0, "terms": 0}


def clear_checkpoint(
    file_bytes: bytes,
    profile: str,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
):
    source_language, target_language = validate_language_pair(source_language, target_language)
    h = hashlib.md5(file_bytes).hexdigest()[:8]
    ckpt_root = ROOT / "output" / "_checkpoints"
    if not ckpt_root.exists():
        return
    for d in ckpt_root.iterdir():
        if d.is_dir() and h in d.name and profile in d.name:
            meta = load_ckpt_meta(str(d))
            try:
                saved_pair = validate_language_pair(
                    meta.get("source_language", DEFAULT_SOURCE_LANGUAGE),
                    meta.get("target_language", DEFAULT_TARGET_LANGUAGE),
                )
            except ValueError:
                continue
            if saved_pair != (source_language, target_language):
                continue
            shutil.rmtree(d)


def save_persisted_config(cfg: RunConfig):
    data = {name: getattr(cfg, name) for name in PERSISTED_CONFIG_FIELDS}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    temp_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        temp_file.chmod(0o600)
    except OSError:
        pass
    temp_file.replace(CONFIG_FILE)


def resolve_service_settings(cfg: RunConfig, service: str) -> ServiceSettings:
    if service == "main":
        return ServiceSettings(cfg.api_key.strip(), cfg.api_base.strip(), cfg.model.strip())
    if service not in {"ner", "embedding", "detect"}:
        raise ValueError(f"未知 API 服务：{service}")
    use_main = bool(getattr(cfg, f"{service}_use_main"))
    api_key = cfg.api_key if use_main else (getattr(cfg, f"{service}_api_key") or cfg.api_key)
    api_base = cfg.api_base if use_main else (getattr(cfg, f"{service}_api_base") or cfg.api_base)
    model = getattr(cfg, f"{service}_model") or cfg.model
    return ServiceSettings(api_key.strip(), api_base.strip(), model.strip())


# ═══════════════════════════════════════════════════════════
# Model presets
# ═══════════════════════════════════════════════════════════

PRESET_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "gpt-4o",
    "gpt-4-turbo",
]

MODELS_FILE = ROOT / ".ui_models.json"
CONFIG_FILE = ROOT / ".ui_config.json"


def _load_model_data() -> dict:
    if MODELS_FILE.exists():
        try:
            return json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"custom": [], "disabled_presets": []}


def _save_model_data(data: dict):
    MODELS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def get_all_models() -> List[str]:
    data = _load_model_data()
    presets = [m for m in PRESET_MODELS if m not in data.get("disabled_presets", [])]
    custom = [m for m in data.get("custom", []) if m not in PRESET_MODELS]
    return presets + custom


def add_custom_model(name: str):
    name = name.strip()
    if not name:
        return
    data = _load_model_data()
    if name not in PRESET_MODELS and name not in data.get("custom", []):
        data.setdefault("custom", []).append(name)
    data.setdefault("disabled_presets", [])
    if name in data["disabled_presets"]:
        data["disabled_presets"].remove(name)
    _save_model_data(data)


def add_model_and_select(cfg: RunConfig, name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    add_custom_model(name)
    cfg.model = name
    save_persisted_config(cfg)
    return name


def remove_model(name: str):
    data = _load_model_data()
    if name in PRESET_MODELS:
        disabled = data.setdefault("disabled_presets", [])
        if name not in disabled:
            disabled.append(name)
    else:
        custom = data.setdefault("custom", [])
        if name in custom:
            custom.remove(name)
    _save_model_data(data)


PROFILE_SKELETON = """\
# ============================================================
#  游戏术语提取 — 项目配置模板
#  请参照注释填写，完成后上传到工具的「高级设置 → 上传新配置」
# ============================================================

# ── 游戏类型 ──────────────────────────────────────────────
# 描述你的游戏整体风格，会写入 AI 的 system prompt。
# 示例:
#   古风武侠开放世界（燕云十六州）
#   二次元卡牌RPG（原神风格）
#   中世纪奇幻MMORPG
game_type: ""


# ── 核心提取原则 ─────────────────────────────────────────
# 告诉 AI 最重要的提取策略，追加到 system prompt 开头。
# 多行文本，每行开头两个空格，用 | 保留换行。
# 示例:
#   core_principle: |
#     文本中出现的人名一律提取，无论主线 NPC 还是路人。
#     地名、建筑、门派只要有专有名称就提取。
#     武功招式、技能名、道具名出现即术语。
#     不提取泛称（如"大侠"、"弟子"）和时间词（如"子时"）。
core_principle: ""


# ── 术语分类 ──────────────────────────────────────────────
# AI 可选的术语分类名，每个术语必须归入其中一类。
# 分类要覆盖你游戏中所有可能的术语类型。
# 示例分类:
#   - "NPC名"       # 角色全名、称谓组合
#   - "BOSS名"      # 首领/精英怪
#   - "地名建筑"    # 城镇、区域、建筑
#   - "门派势力"    # 帮会、组织、阵营
#   - "武学招式"    # 武功、技能、法术
#   - "道具物品"    # 武器、防具、材料
#   - "代币货币"    # 金币、宝石等
#   - "任务名"      # 任务/副本名称
#   - "UI系统"      # 菜单、按钮、系统提示
#   - "成就称号"    # 奖杯、头衔、称号
term_categories:
  - ""  # 在此填写，删掉或复制行


# ── 提取正例（✅ 应该提取的） ──────────────────────────────
# 描述哪些内容应该被 AI 识别为术语。每行一条规则，越具体越好。
# 每条规则格式: "类型描述 + 具体例子（2-3个为佳）"
# 示例:
#   - "武学招式，如：积矩九剑、无名剑法、千机索天·绝"
#   - "NPC全名/称谓，如：张友友、老徐、鹫长老、青儿"
#   - "重要地点建筑，如：千年渡、未央城、神仙渡"
#   - "门派势力，如：墨门、金刀镖局、天工阁"
#   - "核心道具/圣物，如：浮云匙、墨筹、沦波舟"
#   - "战斗BUFF/状态，如：溃势、定身、震慑、真气护盾"
#   - "玩法机制，如：大衍迷阵、水转百戏、白日参辰"
#   - "路人NPC称呼，如：王婶、老农、皮老大、戈老三"
#   - "排行命名，如：戈老大、戈老二、钱二娘、刘三妹"
#   - "动物名角色，如：青、燕、隼、鸢、鸦（作角色名时）"
extract_examples:
  include:
    - ""  # 在此填写


# ── 提取负例（❌ 不应该提取的） ────────────────────────────
# 描述哪些内容看起来像术语，但实际不应该提取。
# 每条规则格式: "类型描述 + 具体例子"
# 示例:
#   - "通用日常物品，如：木炭、草鞋、火把、绳子、箱子"
#   - "通用称谓/泛称，如：大侠、少侠、公子、弟子、先生"
#   - "成语/固定短语，如：一飞冲天、大鹏展翅"
#   - "时间词/节气，如：子时、昨夜、春分"
#   - "通用场所（无专名），如：书房、医馆、夜市、渡口"
#   - "通用原材料，如：柴火、石灰、石炭、芸香"
  exclude:
    - ""  # 在此填写


# ── 可过滤分类 ───────────────────────────────────────────
# 这些分类的术语在提取后会被自动过滤（不输出到结果）。
# 用途：AI 提取了太多噪音术语时，在此列出对应的分类名。
# 示例:
#   - "通用物品"    # 太泛的日常物品
#   - "通用NPC"     # 没有专名的路人
filterable_categories:
  - ""  # 可选，没有可留空或删除


# ── 称谓后缀 ──────────────────────────────────────────────
# 用于去重：当 AI 提取了 "冯大哥" 和 "冯" 两个术语时，
# 如果 "大哥" 在此列表中，只保留 "冯"，删除 "冯大哥"。
# 示例:
#   - "大哥"
#   - "大嫂"
#   - "师兄"
#   - "师姐"
#   - "兄"
#   - "哥"
#   - "嫂"
#   - "叔"
#   - "爷"
address_suffixes:
  - ""  # 可选，没有可留空或删除


# ── 补充提取规则 ─────────────────────────────────────────
# 额外的提取规则，追加到 user prompt 中。
# 示例:
#   - "中文人名模式强制提取：老X、小X、阿X、X嫂、X叔、X爷、X哥、X弟"
#   - "通用场所不提取：书房、医馆、夜市等无专有名称的场所"
#   - "节气/泛时间词不提取：春分、秋分、子时"
extraction_notes:
  - ""  # 可选，没有可留空或删除


# ── 翻译规则 ──────────────────────────────────────────────
# 翻译风格指南，影响 LLM 翻译输出。每行一条规则。
# 示例:
#   - "角色名/NPC名 → 音译（拼音）"
#   - "技能/招式/武学名 → 意译，传达含义"
#   - "地点/建筑名 → 意译为主，必要时音译+意译结合"
#   - "物品/道具名 → 简洁意译"
#   - "古籍/文献名 → 意译为主"
#   - "怪物/BOSS名 → 意译，传达特征"
#   - "头衔称谓 → 意译（如 公子→Master, 长老→Elder）"
translation_rules:
  - ""  # 可选，没有可留空或删除


# ── Few-Shot 示例（高级） ─────────────────────────────────
# 给 AI 的输入→输出示例，帮助 AI 理解提取格式。
# 填写格式:
#   - input: "输入文本"
#     output:
#       - term: "术语名"
#         category: "分类"
# 示例:
#   - input: "青长老说墨门弟子需恪守门规"
#     output:
#       - term: "青长老"
#         category: "NPC名"
#       - term: "墨门"
#         category: "门派势力"
#       - term: "门规"
#         category: "门派系统"
#   - input: "用木炭生火，草鞋踩在石板上嚓嚓作响"
#     output: []
#   - input: "长老说弟子们不得擅自出门"
#     output: []
fewshot_examples: []
# 留空表示不使用 few-shot


# ── 规则抽取器（高级，一般无需修改） ──────────────────────
# 基于规则的预提取，在 LLM 之前运行。目前支持:
#   surname_names:  # 基于百家姓的人名规则抽取
#     surnames: "赵钱孙李..."   # 百家姓字符串
#     min_len: 2                # 最小候选长度
#     max_len: 3                # 最大候选长度
rule_extractors: {}
# 留空表示不使用
"""


def get_profile_template() -> str:
    return PROFILE_SKELETON


def get_profile_content(name: str) -> str:
    p = ROOT / "profiles" / f"{name}.yaml"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


# ═══════════════════════════════════════════════════════════
# Profile management
# ═══════════════════════════════════════════════════════════

def list_profiles() -> List[str]:
    pdir = ROOT / "profiles"
    if not pdir.exists():
        return []
    return sorted([p.stem for p in pdir.glob("*.yaml")])


def save_uploaded_profile(filename: str, content: bytes) -> str:
    import yaml as _yaml
    _yaml.safe_load(content)
    stem = Path(filename).stem
    pdir = ROOT / "profiles"
    pdir.mkdir(exist_ok=True)
    (pdir / f"{stem}.yaml").write_bytes(content)
    return stem


def delete_profile(name: str):
    p = ROOT / "profiles" / f"{name}.yaml"
    if p.exists():
        p.unlink()


# ═══════════════════════════════════════════════════════════
# API test
# ═══════════════════════════════════════════════════════════

def reset_embed_db() -> int:
    """Delete the glossary embedding DB. Returns 1 if deleted, 0 if not found."""
    db_path = ROOT / "database" / "glossary_embeddings.db"
    if db_path.exists():
        db_path.unlink()
        return 1
    return 0


def embed_db_term_count() -> int:
    db_path = ROOT / "database" / "glossary_embeddings.db"
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    except Exception:
        return 0


def test_api_connection(cfg: RunConfig, service: str = "main") -> tuple:
    settings = resolve_service_settings(cfg, service)
    label = SERVICE_LABELS.get(service, service)
    if not settings.api_key:
        return False, f"{label}缺少 API 密钥"
    if not settings.api_base:
        return False, f"{label}缺少 API 服务地址"
    if not settings.model:
        return False, f"{label}缺少模型 ID"
    try:
        from openai import OpenAI
        from core.responses import chat_content, json_object, parse_entities, validate_terms
        from core.header_detect import _source_mapping
        import math
        client = OpenAI(api_key=settings.api_key, base_url=settings.api_base, timeout=30, max_retries=0)
        try:
            if service == "embedding":
                response = client.embeddings.create(model=settings.model, input=["连接测试"])
                data = getattr(response, "data", None)
                vector = getattr(data[0], "embedding", None) if data else None
                if not isinstance(vector, list) or not vector or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                    raise RuntimeError("服务未返回有效向量数据")
            else:
                samples = {
                    "main": '分析无术语文本，严格返回 JSON：{"terms": []}',
                    "ner": '分析无命名实体文本，严格返回 JSON：{"persons": [], "places": []}',
                    "detect": '列为 [0] ID, [1] 中文, [2] EN。严格返回 JSON：{"text_col": 1, "key_col": 0, "en_col": 2}',
                }
                response = client.chat.completions.create(
                    model=settings.model, messages=[{"role": "user", "content": samples[service]}],
                    temperature=0, max_tokens=256,
                )
                content = chat_content(response)
                if service == "main":
                    validate_terms(json_object(content))
                elif service == "ner":
                    parse_entities(content)
                else:
                    _source_mapping(json_object(content), 3)
        finally:
            client.close()
        return True, f"{label}连接成功（{settings.model}）"
    except Exception as e:
        message = str(e)
        for secret in (
            cfg.api_key, cfg.ner_api_key, cfg.embedding_api_key, cfg.detect_api_key,
        ):
            if secret:
                message = message.replace(secret, "***")
        return False, f"{label}连接失败：{message}"


# ═══════════════════════════════════════════════════════════
# Processing task
# ═══════════════════════════════════════════════════════════

class _InterruptedError(Exception):
    pass


class ProcessingTask:
    def __init__(self):
        self.done = False
        self.stage: str = "idle"
        self.stage_done: int = 0
        self.stage_total: int = 0
        self.results: list = []
        self.error: str | None = None
        self.info: str = ""
        self.output_dir: str = ""
        self.output_files: tuple[str, str] | None = None
        self.source_path: str = ""
        self.run_config: RunConfig | None = None
        self._stop = threading.Event()
        self._cancelling = False
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._stop.set()
        self._cancelling = True

    def start(self, source_path: str, glossary_path: str, cfg: RunConfig,
              on_done: Callable = None, src_col: int = 0, gl_cn_col: int = 0, gl_en_col: int = 1,
              src_en_col: int = 1, src_bytes: bytes = b"", key_col: int = None,
              checkpoint_dir: str = ""):
        cfg_snapshot = replace(cfg)
        self.done = False
        self.error = None
        self.results = []
        self.info = ""
        self.output_dir = ""
        self.output_files = None
        self.source_path = source_path
        self.run_config = cfg_snapshot
        self.stage = "starting"
        self.stage_done = 0
        self.stage_total = 1
        self._cancelling = False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(source_path, glossary_path, cfg_snapshot, on_done,
                                     src_col, gl_cn_col, gl_en_col, src_en_col, src_bytes, key_col,
                                     checkpoint_dir), daemon=True
        )
        self._thread.start()

    def _progress(self, stage: str, done: int, total: int, info: str = ""):
        if self._stop.is_set():
            raise _InterruptedError("Cancelled by user")
        self.stage = stage
        self.stage_done = done
        self.stage_total = total
        self.info = info

    def _run(self, source_path: str, glossary_path: str, cfg: RunConfig, on_done,
             src_col: int = 0, gl_cn_col: int = 0, gl_en_col: int = 1, src_en_col: int = 1,
             src_bytes: bytes = b"", key_col: int = None, checkpoint_dir: str = ""):
        try:
            import time as _time
            from core.main import run_pipeline, PipelineOpts, save_outputs
            from core.checkpoint import clear as ckpt_clear

            ts = _time.strftime("%Y%m%d_%H%M%S")
            out_dir = ROOT / "output" / f"run_{ts}"
            out_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir = str(out_dir)

            ckpt_root = ROOT / "output" / "_checkpoints"
            ckpt_root.mkdir(parents=True, exist_ok=True)
            ckpt_dir = checkpoint_dir or str(ckpt_root / checkpoint_dir_name(
                src_bytes,
                cfg.profile,
                cfg.source_language,
                cfg.target_language,
            ))
            Path(ckpt_dir).mkdir(parents=True, exist_ok=True)

            ner_service = resolve_service_settings(cfg, "ner")
            embedding_service = resolve_service_settings(cfg, "embedding")
            opts = PipelineOpts(
                source_language=cfg.source_language,
                target_language=cfg.target_language,
                bilingual=cfg.bilingual, no_translate=cfg.no_translate,
                src_col=src_col, src_en_col=src_en_col, key_col=key_col,
                gl_cn_col=gl_cn_col, gl_en_col=gl_en_col,
                embed_workers=cfg.embed_workers,
                max_concurrent=cfg.max_concurrent,
                max_tokens=cfg.max_tokens,
                flag_unsourced=cfg.flag_unsourced,
                ner_use_main=cfg.ner_use_main,
                ner_api_key=ner_service.api_key,
                ner_base_url=ner_service.api_base,
                ner_model=ner_service.model,
                embedding_use_main=cfg.embedding_use_main,
                embedding_api_key=embedding_service.api_key,
                embedding_base_url=embedding_service.api_base,
                embedding_model=embedding_service.model,
            )
            results = run_pipeline(
                source_path, glossary_path, cfg.profile,
                cfg.api_key, cfg.api_base, cfg.model,
                output_dir=str(out_dir), raw_dir=str(out_dir / "raw_data"),
                progress_callback=self._progress,
                checkpoint_dir=ckpt_dir,
                opts=opts,
            )
            output_files = save_outputs(
                results, out_dir,
                profile_name=cfg.profile,
                source_path=source_path,
            )
            ckpt_clear(ckpt_dir)
            self.results = results
            self.output_files = tuple(str(path) for path in output_files)
            self.stage = "done"
            self.stage_done = 1
            self.stage_total = 1
            self.info = f"已提取并保存 {len(results)} 条术语"
        except _InterruptedError:
            self.error = "Cancelled by user"
        except Exception as e:
            self.stage = "incomplete"
            self.error = f"{type(e).__name__}: {e}"
            logging.getLogger("pipeline").exception("Pipeline failed")
        finally:
            self.done = True
            if on_done:
                on_done()
