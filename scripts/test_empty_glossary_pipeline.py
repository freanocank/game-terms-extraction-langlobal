#!/usr/bin/env python3
"""Regression: source-only runs must not clear or reuse glossary embeddings."""

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import core.main as main


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


embed_store_calls = []
translation_store = []


class UnexpectedEmbedStore:
    def __init__(self, *args, **kwargs):
        embed_store_calls.append((args, kwargs))


def fake_extract(texts, *args, **kwargs):
    return [{"term": "墨门", "category": "门派势力", "source_text": texts[0], "source_key": "A1"}]


def fake_translate(extracted, glossary, profile, api_key, base_url, model, **kwargs):
    translation_store.append(kwargs.get("embed_store"))
    return [{**extracted[0], "translation": "Momen", "match_type": "llm_translated"}]


main.EmbedStore = UnexpectedEmbedStore
main.extract_terms = fake_extract
main.match_and_translate = fake_translate


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    source_path = tmp_path / "source.xlsx"
    glossary_path = tmp_path / "empty_glossary.xlsx"
    pd.DataFrame({"文本 ID": ["A1"], "中文原文": ["墨门弟子"]}).to_excel(source_path, index=False)
    glossary_path.write_bytes(main.empty_glossary_bytes())

    output = main.run_pipeline(
        str(source_path), str(glossary_path), "yanyun",
        api_key="test-key", base_url="https://example.invalid/v1", model="test-model",
        output_dir=str(tmp_path / "output"),
        opts=main.PipelineOpts(src_col=1, key_col=0),
    )

ck("empty glossary does not construct or sync the embedding store", not embed_store_calls, embed_store_calls)
ck("source-only translation receives no stale reference store", translation_store == [None], translation_store)
ck("source-only pipeline still returns translated terms",
   output[0]["translation"] == "Momen" and output[0]["match_type"] == "llm_translated", output)


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
