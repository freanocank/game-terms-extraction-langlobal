#!/usr/bin/env python3
"""Deterministic regression tests for context-aware header detection cache."""

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.header_detect as hd


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


temp_root = Path(tempfile.mkdtemp())
hd.CACHE_FILE = temp_root / "header_cache.json"
base_a = "https://detect.example/v1"
model_a = "detect-model-a"
common = {"api_key": "cache-test-key-a", "base_url": base_a, "model": model_a}

ck("legacy signature keeps ordered headers", (
    hd._sig(["Key", "原文"], "source") != hd._sig(["原文", "Key"], "source")
))

df = pd.DataFrame({
    "编号": ["A,1", "A,2"],
    "正文": ["勇者拿起烈焰之剑", "艾莉娅施放治愈魔法"],
})
calls = []
real_ai = hd._ai_detect


def fake_ai(dataframe, file_type, **kwargs):
    calls.append({"kind": file_type, "kwargs": kwargs, "sample": dataframe.iloc[0, 0]})
    if file_type == "source":
        return {"text_col": 1, "key_col": 0, "en_col": None}
    return {"cn_col": 0, "en_col": 1}


hd._ai_detect = fake_ai
r1 = hd.detect_source_column(df, **common)
cached = hd._cache_get_context(df, "source", base_a, model_a)
ck("fresh context calls AI", r1["method"] == "ai" and r1["text_col"] == 1, r1)
ck("AI result is stored as schema-v2 context", (
    cached is not None and cached.get("source") == "ai" and cached.get("schema") == 2
), cached)
cache_text = hd.CACHE_FILE.read_text(encoding="utf-8")
ck("cache persists neither API key nor raw sample", (
    common["api_key"] not in cache_text and "勇者拿起烈焰之剑" not in cache_text
))

before = len(calls)
r2 = hd.detect_source_column(
    df,
    api_key="cache-test-key-b",
    base_url=base_a + "/",
    model=model_a,
)
ck("same context hits cache despite key change and trailing slash", (
    r2["method"] == "cache" and r2.get("cache_source") == "ai" and len(calls) == before
), r2)

changed_sample = df.copy()
changed_sample.iloc[0, 0] = "B,9"
before = len(calls)
r3 = hd.detect_source_column(changed_sample, **common)
ck("sample change invalidates AI cache", r3["method"] == "ai" and len(calls) == before + 1, r3)

before = len(calls)
r4 = hd.detect_source_column(df, **{**common, "model": "detect-model-b"})
ck("model change invalidates AI cache", r4["method"] == "ai" and len(calls) == before + 1, r4)

before = len(calls)
r5 = hd.detect_source_column(df, **{**common, "base_url": "https://other.example/v1"})
ck("provider URL change invalidates AI cache", r5["method"] == "ai" and len(calls) == before + 1, r5)

legacy_df = pd.DataFrame({"记录号": ["L1"], "正文内容": ["墨门弟子"]})
hd._cache_put(
    list(legacy_df.columns),
    "source",
    {"text_col": 0, "key_col": 1, "en_col": None},
    "ai",
)
before = len(calls)
legacy_result = hd.detect_source_column(legacy_df, **common)
ck("legacy AI entry is ignored because it lacks context", (
    legacy_result["method"] == "ai" and len(calls) == before + 1
), legacy_result)

user_df = pd.DataFrame({"槽位A": ["U1"], "槽位B": ["正确正文"]})
hd.record_source_correction(
    list(user_df.columns),
    text_col=1,
    key_col=0,
    en_col=None,
    df=user_df,
    base_url=base_a,
    model=model_a,
)
user_result = hd.detect_source_column(
    user_df,
    api_key="another-key",
    base_url=base_a + "/",
    model=model_a,
)
ck("human correction hits only its exact v2 context", (
    user_result["method"] == "cache"
    and user_result.get("cache_source") == "user"
    and user_result["text_col"] == 1
), user_result)

user_changed_sample = pd.DataFrame({"槽位A": ["正确正文"], "槽位B": ["U1"]})
before = len(calls)
changed_user_result = hd.detect_source_column(user_changed_sample, **common)
ck("human correction does not leak across different samples", (
    changed_user_result["method"] == "ai" and len(calls) == before + 1
), changed_user_result)

legacy_user_df = pd.DataFrame({"通用列A": ["K1"], "通用列B": ["正文"]})
hd._cache_put(
    list(legacy_user_df.columns),
    "source",
    {"text_col": 0, "key_col": 1, "en_col": None},
    "user",
)
before = len(calls)
legacy_user_result = hd.detect_source_column(legacy_user_df, **common)
ck("legacy user entry is a safe miss instead of a global override", (
    legacy_user_result["method"] == "ai" and len(calls) == before + 1
), legacy_user_result)

no_context_df = pd.DataFrame({"旧接口A": ["K1"], "旧接口B": ["正文"]})
recorded_without_context = hd.record_source_correction(
    list(no_context_df.columns), text_col=1, key_col=0, en_col=None
)
ck("legacy correction API reports that missing context was not persisted", (
    recorded_without_context is False
    and hd._cache_get(list(no_context_df.columns), "source") is None
))

hd._cache_put_context(
    user_df,
    "source",
    {"text_col": 0, "key_col": None, "en_col": None},
    "ai",
    base_a,
    model_a,
)
protected = hd.detect_source_column(user_df, **common)
ck("AI context cannot outrank a human-confirmed mapping", (
    protected["text_col"] == 1 and protected.get("cache_source") == "user"
), protected)

seed_df = pd.DataFrame({"SeedKey": ["S1"], "SeedText": ["人工认为第一列是正文"]})
hd._cache_put(
    list(seed_df.columns),
    "source",
    {"text_col": 1, "key_col": 0, "en_col": None},
    "seed",
)
hd.record_source_correction(
    list(seed_df.columns),
    text_col=0,
    key_col=1,
    en_col=None,
    df=seed_df,
    base_url=base_a,
    model=model_a,
)
same_seed_context = hd.detect_source_column(seed_df, **common)
other_seed_context = hd.detect_source_column(
    pd.DataFrame({"SeedKey": ["S2"], "SeedText": ["另一个样本"]}),
    **common,
)
ck("same-context human correction outranks a shipped seed", (
    same_seed_context.get("cache_source") == "user" and same_seed_context["text_col"] == 0
), same_seed_context)
ck("shipped seed remains the fallback for a different context", (
    other_seed_context.get("cache_source") == "seed" and other_seed_context["text_col"] == 1
), other_seed_context)

tenant_a, _ = hd._context_signature(df, "source", "https://tenant-a@example.com/v1", model_a)
tenant_b, _ = hd._context_signature(df, "source", "https://tenant-b@example.com/v1", model_a)
bad_port, _ = hd._context_signature(df, "source", "https://example.com:bad/v1", model_a)
normal_port, _ = hd._context_signature(df, "source", "https://example.com/v1", model_a)
ck("Base URL userinfo and malformed ports cannot collide", (
    tenant_a != tenant_b and bad_port != normal_port
))

corrupt_df = pd.DataFrame({"损坏缓存A": ["C1"], "损坏缓存B": ["有效正文"]})
hd.CACHE_FILE.write_text("[]", encoding="utf-8")
corrupt_top = hd.detect_source_column(corrupt_df, **common)
ck("non-object cache root becomes a safe miss", corrupt_top["method"] == "ai", corrupt_top)

context_key, _ = hd._context_signature(corrupt_df, "source", base_a, model_a)
hd.CACHE_FILE.write_text('{"' + context_key + '": "bad-entry"}', encoding="utf-8")
corrupt_entry = hd.detect_source_column(corrupt_df, **common)
ck("malformed cache entry becomes a safe miss", corrupt_entry["method"] == "ai", corrupt_entry)

glossary = pd.DataFrame({"中文词": ["剑"], "译文": ["sword"]})
glossary_first = hd.detect_glossary_columns(glossary, **common)
glossary_second = hd.detect_glossary_columns(glossary, **common)
ck("glossary path writes then reads context cache", (
    glossary_first["method"] == "ai"
    and glossary_second["method"] == "cache"
    and glossary_second.get("cache_source") == "ai"
), {"first": glossary_first, "second": glossary_second})

hd._ai_detect = real_ai
hd.CACHE_FILE = ROOT / "header_cache.json"
seed_result = hd.detect_source_column(
    pd.DataFrame({"Key": ["Dialog,001"], "原文": ["勇者拿起了烈焰之剑"]}),
    api_key="",
    base_url=base_a,
    model=model_a,
)
ck("shipped seed remains backward compatible", (
    seed_result["method"] == "cache"
    and seed_result.get("cache_source") == "seed"
    and seed_result["text_col"] == 1
), seed_result)

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
