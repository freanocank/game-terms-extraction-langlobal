#!/usr/bin/env python3
"""Streamlit regression checks for the localized results view and charts."""

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


sample_terms = [
    {"term": "墨门", "category": "门派势力", "translation": "Momen", "match_type": "exact",
     "source_key": "A1", "source_text": "墨门弟子"},
    {"term": "青长老", "category": "NPC 名", "translation": "Elder Qing", "match_type": "llm_translated",
     "source_key": "A1", "source_text": "青长老抵达"},
    {"term": "浮云匙", "category": "道具物品", "translation": "Cloud Key", "match_type": "bilingual",
     "source_key": "A2", "source_text": "获得浮云匙"},
    {"term": "墨筹", "category": "道具物品", "translation": "", "match_type": "no_translate",
     "source_key": "A3", "source_text": "交付墨筹"},
]


at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=15).run()
at.session_state["task_results"] = sample_terms
at.radio[0].set_value("结果与导出").run()

ck("results page renders without exception", not at.exception,
   [item.message for item in at.exception])
metric_labels = [item.label for item in at.metric]
ck("summary metrics use clear Chinese labels",
   metric_labels == ["术语总数", "术语分类", "沿用术语表", "AI 生成译文"], metric_labels)
ck("translation source and category charts both render", len(at.get("vega_lite_chart")) == 2,
   len(at.get("vega_lite_chart")))
ck("translation-source counts are readable without hover", any(
    "沿用术语表 1 条（25.0%）" in item.value and "AI 翻译 1 条（25.0%）" in item.value
    for item in at.caption
), [item.value for item in at.caption])

table_columns = list(at.dataframe[0].value.columns) if at.dataframe else []
ck("result table replaces internal field names", table_columns == [
    "中文术语", "术语分类", "英文译文", "译文来源", "文本 ID", "来源原文"
], table_columns)

download_labels = [item.label for item in at.get("download_button")]
ck("export actions state their file purpose", download_labels == [
    "下载标注模板（.xlsx）", "下载完整结果（.xlsx）"
], download_labels)

many_categories = [
    {"term": f"术语{i}", "category": f"分类{i:02d}", "translation": f"Term {i}", "match_type": "exact"}
    for i in range(15)
]
at.session_state["task_results"] = many_categories
at.run()
full_category_tables = [
    item.value for item in at.dataframe
    if list(item.value.columns) == ["术语分类", "数量", "占比"]
]
ck("many categories are grouped in the chart with a complete readable table",
   any("数量最多的 11 类" in item.value for item in at.caption)
   and len(full_category_tables) == 1 and len(full_category_tables[0]) == 15,
   ([item.value for item in at.caption], [len(value) for value in full_category_tables]))


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
