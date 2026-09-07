#!/usr/bin/env python3
"""Verify configurable LLM-based header detection.

Live cases require --live and DETECT_API_KEY or OPENAI_API_KEY. The
fallback case is always offline and uses an isolated cache.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import core.header_detect as hd
from core.header_detect import detect_source_column, detect_glossary_columns

TEMPLATE_8COL = pd.DataFrame({
    "Key值": ["UI_SKILL_001", "NPC_NAME_017"],
    "术语分类": ["技能", "人名"],
    "术语原文": ["八荒剑诀", "青长老"],
    "术语译文": ["Eight Wilds Sword Art", "Elder Qing"],
    "备注": ["", "门派长老"],
    "来源原文": ["习得八荒剑诀第一式", "青长老说墨门弟子需恪守门规"],
    "审核状态": ["已审核", "未审核"],
    "最新修订时间": ["2026-06-10T12:00:00", "2026-06-11T09:30:00"],
})

CLASSIC_2COL = pd.DataFrame({
    "中文": ["墨门", "八荒剑诀"],
    "英文": ["Momen", "Eight Wilds Sword Art"],
})

SWAPPED_2COL = pd.DataFrame({
    "English": ["Momen", "Elder Qing"],
    "术语": ["墨门", "青长老"],
})

SOURCE_3COL = pd.DataFrame({
    "Key": ["DLG_001", "DLG_002"],
    "原文文本": ["青长老说墨门弟子需恪守门规，不得擅自下山。", "你习得了八荒剑诀第一式，去找师父复命吧。"],
    "备注": ["", "新手引导"],
})

SOURCE_BILINGUAL = pd.DataFrame({
    "Key": ["DLG_001", "DLG_002"],
    "中文": ["青长老说墨门弟子需恪守门规。", "你习得了八荒剑诀第一式。"],
    "English": ["Elder Qing said Momen disciples must obey the rules.",
                "You have learned the first form of Eight Wilds Sword Art."],
})

results = []


def check(name, actual, expected):
    ok = all(actual.get(k) == v for k, v in expected.items())
    results.append(ok)
    detail = "" if ok else f"  expected={expected} actual={actual}"
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{detail}")


api_key = os.getenv("DETECT_API_KEY") or os.getenv("OPENAI_API_KEY", "")
base_url = os.getenv("DETECT_API_BASE") or os.getenv("DETECT_BASE_URL") or os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
model = os.getenv("DETECT_MODEL") or os.getenv("OPENAI_MODEL")

if "--live" in sys.argv and api_key:
    hd.CACHE_FILE = Path(tempfile.mkdtemp()) / "live_header_cache.json"
    common = {"api_key": api_key, "base_url": base_url, "model": model}
    r = detect_glossary_columns(TEMPLATE_8COL, **common)
    check("8-col template -> cn=2(术语原文), en=3(术语译文)", r, {"cn_col": 2, "en_col": 3, "method": "ai"})

    r = detect_glossary_columns(CLASSIC_2COL, **common)
    check("classic 2-col -> cn=0, en=1", r, {"cn_col": 0, "en_col": 1, "method": "ai"})

    r = detect_glossary_columns(SWAPPED_2COL, **common)
    check("swapped 2-col (EN first) -> cn=1, en=0", r, {"cn_col": 1, "en_col": 0, "method": "ai"})

    r = detect_source_column(SOURCE_3COL, **common)
    check("source 3-col (key/text/note) -> text=1, key=0, no en", r,
          {"text_col": 1, "key_col": 0, "en_col": None, "method": "ai"})

    r = detect_source_column(SOURCE_BILINGUAL, **common)
    check("bilingual source -> text=1, key=0, en=2", r,
          {"text_col": 1, "key_col": 0, "en_col": 2, "method": "ai"})
else:
    print("SKIP | live header detection (requires --live and configured API key)")

hd.CACHE_FILE = Path(tempfile.mkdtemp()) / "offline_header_cache.json"
class APIConnectionError(Exception):
    pass


with patch.object(hd, "_ai_detect", side_effect=APIConnectionError("offline fixture")):
    r = detect_glossary_columns(
        TEMPLATE_8COL, api_key="offline-test-key",
        base_url="https://offline.example.invalid/v1", model="offline-test-model",
    )
check("unreachable API -> default 0/1 fallback", r, {"cn_col": 0, "en_col": 1, "method": "default"})
check("unreachable API exposes a stable reason", r, {
    "fallback_code": "connection_failed",
    "fallback_reason": "无法连接表头识别服务",
})

hd.CACHE_FILE = Path(tempfile.mkdtemp()) / "missing_key_cache.json"
r = detect_source_column(
    SOURCE_3COL,
    api_key="",
    base_url="https://detect.example/v1",
    model="detect-model",
)
check("missing key is visible and remains manually editable", r, {
    "text_col": 0,
    "method": "default",
    "fallback_code": "missing_api_key",
    "fallback_reason": "未配置表头识别 API 密钥",
})

real_ai = hd._ai_detect


def invalid_mapping(*args, **kwargs):
    return {"text_col": 99, "key_col": 0, "en_col": None}


hd._ai_detect = invalid_mapping
hd.CACHE_FILE = Path(tempfile.mkdtemp()) / "invalid_mapping_cache.json"
r = detect_source_column(
    SOURCE_3COL,
    api_key="safe-test-key",
    base_url="https://detect.example/v1",
    model="detect-model",
)
check("invalid model mapping is explicit", r, {
    "method": "default",
    "fallback_code": "invalid_mapping",
    "fallback_reason": "模型返回的中文源文本列索引无效",
})


def leaking_provider_error(*args, **kwargs):
    raise RuntimeError("provider rejected safe-test-key")


hd._ai_detect = leaking_provider_error
hd.CACHE_FILE = Path(tempfile.mkdtemp()) / "redaction_cache.json"
r = detect_source_column(
    SOURCE_3COL,
    api_key="safe-test-key",
    base_url="https://detect.example/v1",
    model="detect-model",
)
check("unexpected provider errors are redacted", r, {
    "method": "default",
    "fallback_code": "provider_error",
    "fallback_reason": "表头识别服务调用失败",
})
check("fallback result does not echo the API key", {"redacted": "safe-test-key" not in str(r)}, {"redacted": True})

hd._ai_detect = real_ai

n_pass = sum(results)
print(f"\n{n_pass}/{len(results)} passed")
sys.exit(0 if n_pass == len(results) else 1)
