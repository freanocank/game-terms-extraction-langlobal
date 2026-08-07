#!/usr/bin/env python3
"""Auto-detect Excel column roles via a configurable OpenAI-compatible service.

Callers may pass key/base/model for the current run. When omitted, DETECT_API_KEY,
DETECT_API_BASE/DETECT_BASE_URL and DETECT_MODEL are resolved from the environment
at call time. If no key is available, the app falls back to manual column mapping.

Persistent header dictionary (`header_cache.json` at repo root): shipped seeds use
ordered headers, while AI suggestions and context-aware human corrections also bind
to the provider, model, prompt version, and a hash of the first three sample rows.
Human-confirmed mappings outrank later AI guesses inside the same context.
"""

import os
import json
import re
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
from openai import OpenAI
from config_template import DEFAULT_API_BASE, DEFAULT_DETECT_MODEL

CACHE_FILE = Path(__file__).resolve().parent.parent / "header_cache.json"
_CACHE_SCHEMA_VERSION = 2
_PROMPT_VERSION = "header-role-v2"


# ── Persistent header dictionary ─────────────────────────────────────────────

def _sig(headers, file_type: str) -> str:
    """Legacy signature used by shipped seeds and human-confirmed mappings."""
    cols = " | ".join(str(h).strip() for h in headers)
    return f"{file_type}::{cols}"


def _normalize_base_url(base_url: str) -> str:
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.hostname:
            return raw.rstrip("/")
        scheme = parts.scheme.lower()
        host = parts.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            return raw.rstrip("/")
        if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            host = f"{host}:{port}"
        if "@" in parts.netloc:
            userinfo = parts.netloc.rsplit("@", 1)[0]
            host = f"{userinfo}@{host}"
        path = parts.path.rstrip("/")
        return urlunsplit((scheme, host, path, parts.query, ""))
    except Exception:
        return raw.rstrip("/")


def _context_signature(df: pd.DataFrame, file_type: str, base_url: str, model: str) -> tuple[str, dict]:
    headers, sample_rows = _headers_and_sample(df, max_rows=3)
    normalized_headers = [str(header).strip() for header in headers]
    normalized_base = _normalize_base_url(base_url)
    normalized_model = str(model or "").strip()
    material = {
        "schema": _CACHE_SCHEMA_VERSION,
        "prompt": _PROMPT_VERSION,
        "file_type": file_type,
        "headers": normalized_headers,
        "sample": sample_rows,
        "api_base": normalized_base,
        "model": normalized_model,
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sample_canonical = json.dumps(sample_rows, ensure_ascii=False, separators=(",", ":"))
    metadata = {
        "schema": _CACHE_SCHEMA_VERSION,
        "context_id": digest[:16],
        "sample_hash": hashlib.sha256(sample_canonical.encode("utf-8")).hexdigest()[:16],
        "provider_hash": hashlib.sha256(normalized_base.encode("utf-8")).hexdigest()[:16],
        "model": normalized_model,
    }
    return f"v{_CACHE_SCHEMA_VERSION}::{file_type}::{digest}", metadata


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict):
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cache_get(headers, file_type: str):
    return _load_cache().get(_sig(headers, file_type))


def _cache_get_context(df: pd.DataFrame, file_type: str, base_url: str, model: str):
    key, _ = _context_signature(df, file_type, base_url, model)
    return _load_cache().get(key)


# authority order: a user's manual choice > a shipped seed > an AI guess.
# A lower-authority source never overwrites a higher one (so AI can't clobber a human fix).
_RANK = {"ai": 0, "seed": 1, "user": 2}


def _cache_put(headers, file_type: str, mapping: dict, source: str):
    data = _load_cache()
    key = _sig(headers, file_type)
    existing = data.get(key)
    if isinstance(existing, dict) and _RANK.get(existing.get("source"), 0) > _RANK.get(source, 0):
        return
    data[key] = {**mapping, "source": source}
    _save_cache(data)


def _cache_put_context(df: pd.DataFrame, file_type: str, mapping: dict,
                       source: str, base_url: str, model: str) -> dict:
    data = _load_cache()
    key, metadata = _context_signature(df, file_type, base_url, model)
    existing = data.get(key)
    if isinstance(existing, dict) and _RANK.get(existing.get("source"), 0) > _RANK.get(source, 0):
        return metadata
    data[key] = {**mapping, "source": source, **metadata}
    _save_cache(data)
    return metadata


def _cache_lookup(df: pd.DataFrame, file_type: str, base_url: str, model: str) -> tuple[dict | None, dict]:
    data = _load_cache()
    legacy = data.get(_sig(df.columns, file_type))
    key, metadata = _context_signature(df, file_type, base_url, model)
    contextual = data.get(key)
    contextual = contextual if isinstance(contextual, dict) else None
    seed = legacy if isinstance(legacy, dict) and legacy.get("source") == "seed" else None
    if contextual and _RANK.get(contextual.get("source"), 0) > _RANK["seed"]:
        return contextual, metadata
    if seed:
        return seed, {"schema": 1, "context_id": ""}
    return contextual, metadata


# ── LLM detection ────────────────────────────────────────────────────────────

class HeaderDetectionError(RuntimeError):
    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


def _failure_details(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HeaderDetectionError):
        return exc.code, exc.user_message
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if status in {401, 403} or "authentication" in name or "permission" in name:
        return "authentication_failed", "API 密钥无效，或当前账号没有该模型的访问权限"
    if status == 429 or "ratelimit" in name:
        return "rate_limited", "请求频率或账户额度受限"
    if "timeout" in name:
        return "timeout", "表头识别请求超时"
    if "connection" in name:
        return "connection_failed", "无法连接表头识别服务"
    if isinstance(status, int) and status >= 400:
        return "provider_error", f"表头识别服务返回 HTTP {status}"
    return "provider_error", "表头识别服务调用失败"

def _headers_and_sample(df: pd.DataFrame, max_rows: int = 4) -> tuple:
    headers = list(df.columns)
    sample_rows = df.head(max_rows).values.tolist()
    sample_rows = [[str(c) if pd.notna(c) else "" for c in row] for row in sample_rows]
    return headers, sample_rows


_DETECT_PROMPT = """你是一个数据分析助手。分析以下 Excel 表格的列名和样本数据，判断每一列的角色。

{context}

列名和样本数据:
{sample}

请只返回一个 JSON 对象，不要任何额外文字:
{output_format}"""


def _resolve_detection_settings(api_key=None, base_url=None, model=None) -> tuple[str, str, str]:
    resolved_key = os.getenv("DETECT_API_KEY", "") if api_key is None else api_key
    resolved_base = (
        os.getenv("DETECT_API_BASE")
        or os.getenv("DETECT_BASE_URL")
        or DEFAULT_API_BASE
    ) if base_url is None else base_url
    resolved_model = (os.getenv("DETECT_MODEL") or DEFAULT_DETECT_MODEL) if model is None else model
    return resolved_key.strip(), resolved_base.strip(), resolved_model.strip()


def _ai_detect(df: pd.DataFrame, file_type: str, *, api_key=None, base_url=None, model=None) -> dict:
    headers, sample_rows = _headers_and_sample(df, max_rows=3)
    sample_text = "列名: " + " | ".join(f"[{i}] {h}" for i, h in enumerate(headers)) + "\n"
    for ri, row in enumerate(sample_rows):
        sample_text += f"第{ri+1}行: " + " | ".join(f"[{i}] {cell}" for i, cell in enumerate(row)) + "\n"

    if file_type == "source":
        context = ("这是一个游戏文本文件，需要找出：1) 哪一列包含**需要提取术语的中文原文文本**；"
                   "2) 哪一列是行唯一标识（Key/ID/编号，通常是字符串编码或数字序号），没有则 key_col 为 null；"
                   "3) 哪一列是与原文**逐行对照的英文译文**（整句翻译，不是备注或分类），没有则 en_col 为 null。")
        output_format = '{"text_col": <列索引数字>, "key_col": <列索引数字或null>, "en_col": <列索引数字或null>}'
    else:
        context = ("这是一个游戏术语表文件，需要找出哪一列是**中文术语原文**，哪一列是对应的**英文译文**。"
                   "表中可能还有 Key、分类、备注、来源、审核状态、修订时间等其他列（如「术语分类」是分类标签而非术语本身），不要选这些列。")
        output_format = '{"cn_col": <列索引数字>, "en_col": <列索引数字>}'

    api_key, base_url, model = _resolve_detection_settings(api_key, base_url, model)
    if not api_key:
        raise HeaderDetectionError("missing_api_key", "未配置表头识别 API 密钥")
    if not base_url or not model:
        raise HeaderDetectionError("invalid_config", "表头识别服务地址或模型 ID 未配置完整")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _DETECT_PROMPT.format(
            context=context, sample=sample_text, output_format=output_format
        )}],
        temperature=0,
        max_tokens=256,
    )
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{[^}]+\}", raw)
    if m:
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise HeaderDetectionError("invalid_response", "模型没有返回可解析的 JSON 表头映射")


# ── Public API ───────────────────────────────────────────────────────────────

def _is_index(value, column_count: int) -> bool:
    return type(value) is int and 0 <= value < column_count


def _source_mapping(raw: dict, column_count: int) -> dict:
    text_col = raw.get("text_col")
    key_col = raw.get("key_col")
    en_col = raw.get("en_col")
    if not _is_index(text_col, column_count):
        raise HeaderDetectionError("invalid_mapping", "模型返回的中文源文本列索引无效")
    if key_col is not None and (not _is_index(key_col, column_count) or key_col == text_col):
        raise HeaderDetectionError("invalid_mapping", "模型返回的文本 ID 列索引无效或与源文本列重复")
    if en_col is not None and (
        not _is_index(en_col, column_count) or en_col == text_col or en_col == key_col
    ):
        raise HeaderDetectionError("invalid_mapping", "模型返回的英文译文列索引无效或与其他列重复")
    return {"text_col": text_col, "key_col": key_col, "en_col": en_col}


def _glossary_mapping(raw: dict, column_count: int) -> dict:
    cn_col = raw.get("cn_col")
    en_col = raw.get("en_col")
    if not _is_index(cn_col, column_count) or not _is_index(en_col, column_count) or cn_col == en_col:
        raise HeaderDetectionError("invalid_mapping", "模型返回的术语原文列或译文列索引无效")
    return {"cn_col": cn_col, "en_col": en_col}


def _cache_result(mapping: dict, cached: dict, metadata: dict) -> dict:
    source = cached.get("source", "ai")
    confidence = {
        "user": "高（人工确认）",
        "seed": "高（内置表头规则）",
        "ai": "中（AI 历史识别，请确认）",
    }.get(source, "中（历史记录，请确认）")
    return {
        **mapping,
        "method": "cache",
        "confidence": confidence,
        "cache_source": source,
        "cache_version": int(cached.get("schema") or metadata.get("schema") or 1),
        "cache_context_id": cached.get("context_id") or metadata.get("context_id") or "",
    }


def _fallback_result(mapping: dict, exc: Exception) -> dict:
    code, reason = _failure_details(exc)
    return {
        **mapping,
        "method": "default",
        "confidence": "低（默认预选，请人工确认）",
        "fallback_code": code,
        "fallback_reason": reason,
    }

def detect_source_column(df: pd.DataFrame, *, api_key=None, base_url=None, model=None) -> dict:
    """Detect source-file column roles and preserve a visible fallback reason."""
    n = len(df.columns)
    resolved_key, resolved_base, resolved_model = _resolve_detection_settings(api_key, base_url, model)
    cached, metadata = _cache_lookup(df, "source", resolved_base, resolved_model)
    if cached:
        try:
            return _cache_result(_source_mapping(cached, n), cached, metadata)
        except (HeaderDetectionError, TypeError, ValueError):
            pass
    try:
        ai = _ai_detect(
            df,
            "source",
            api_key=resolved_key,
            base_url=resolved_base,
            model=resolved_model,
        )
        mapping = _source_mapping(ai, n)
        metadata = _cache_put_context(df, "source", mapping, "ai", resolved_base, resolved_model)
        return {
            **mapping,
            "method": "ai",
            "confidence": "中（AI 建议，请人工确认）",
            "cache_context_id": metadata["context_id"],
        }
    except Exception as exc:
        return _fallback_result({"text_col": 0, "key_col": None, "en_col": None}, exc)


def detect_glossary_columns(df: pd.DataFrame, *, api_key=None, base_url=None, model=None) -> dict:
    """Detect glossary column roles and preserve a visible fallback reason."""
    n = len(df.columns)
    resolved_key, resolved_base, resolved_model = _resolve_detection_settings(api_key, base_url, model)
    cached, metadata = _cache_lookup(df, "glossary", resolved_base, resolved_model)
    if cached:
        try:
            return _cache_result(_glossary_mapping(cached, n), cached, metadata)
        except (HeaderDetectionError, TypeError, ValueError):
            pass
    try:
        ai = _ai_detect(
            df,
            "glossary",
            api_key=resolved_key,
            base_url=resolved_base,
            model=resolved_model,
        )
        mapping = _glossary_mapping(ai, n)
        metadata = _cache_put_context(df, "glossary", mapping, "ai", resolved_base, resolved_model)
        return {
            **mapping,
            "method": "ai",
            "confidence": "中（AI 建议，请人工确认）",
            "cache_context_id": metadata["context_id"],
        }
    except Exception as exc:
        default_mapping = {"cn_col": 0, "en_col": 1 if n >= 2 else 0}
        return _fallback_result(default_mapping, exc)


# ── Learning from the user's manual column choice ────────────────────────────

def record_source_correction(headers, text_col, key_col=None, en_col=None, *,
                             df: pd.DataFrame | None = None,
                             base_url: str = "", model: str = ""):
    """Persist a user's correction; context-aware callers should provide ``df``."""
    mapping = {
        "text_col": int(text_col),
        "key_col": None if key_col is None or int(key_col) < 0 else int(key_col),
        "en_col": None if en_col is None or int(en_col) < 0 else int(en_col),
    }
    if df is None:
        return False
    return _cache_put_context(df, "source", mapping, "user", base_url, model)


def record_glossary_correction(headers, cn_col, en_col, *,
                               df: pd.DataFrame | None = None,
                               base_url: str = "", model: str = ""):
    """Persist a user's correction; context-aware callers should provide ``df``."""
    mapping = {"cn_col": int(cn_col), "en_col": int(en_col)}
    if df is None:
        return False
    return _cache_put_context(df, "glossary", mapping, "user", base_url, model)
