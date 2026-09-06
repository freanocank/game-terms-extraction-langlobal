#!/usr/bin/env python3
"""Streamlit checks for language-pair selection and multilingual column mapping."""

import json
import os
import shutil
import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ui.ui_backend as backend


results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    suffix = f"  >> {str(detail)[:200]}" if detail and not ok else ""
    print(f"{'PASS' if ok else 'FAIL'} | {name}{suffix}")


class FakeProcessingTask:
    calls = []

    def __init__(self):
        self.done = False
        self.error = None
        self.results = []
        self.info = ""
        self.output_dir = ""
        self.output_files = None
        self.source_path = ""
        self.stage = "starting"
        self.stage_done = 0
        self.stage_total = 1
        self._cancelling = False

    @property
    def is_running(self):
        return False

    def start(self, source_path, glossary_path, cfg, **kwargs):
        self.source_path = source_path
        self.calls.append({
            "source_path": source_path,
            "glossary_path": glossary_path,
            "source_language": cfg.source_language,
            "target_language": cfg.target_language,
            "bilingual": cfg.bilingual,
            "no_translate": cfg.no_translate,
            "kwargs": kwargs,
        })

    def cancel(self):
        self._cancelling = True


source_io = BytesIO()
pd.DataFrame({
    "ID": ["D001", "D002"],
    "Note": ["", ""],
    "EN-v2": ["Training Afternoon", "Moonkeep"],
    "DE-MT": ["Trainingsnachmittag", "Mondfeste"],
    "FR-MT": ["Après-midi d’entraînement", "Fort-de-Lune"],
    "CN": ["训练下午", "月之堡"],
}).to_excel(source_io, index=False)

previous_api_key = os.environ.get("OPENAI_API_KEY")
os.environ["OPENAI_API_KEY"] = "test-key"
original_task = backend.ProcessingTask

with tempfile.TemporaryDirectory() as tmp:
    backend.CONFIG_FILE = Path(tmp) / "config.json"
    backend.MODELS_FILE = Path(tmp) / "models.json"
    backend.CONFIG_FILE.write_text(json.dumps({
        "model": "gemini-3.1-flash-lite",
        "profile": "yanyun",
        "api_base": "https://example.invalid/v1",
        "source_language": "en",
        "target_language": "de",
    }), encoding="utf-8")
    backend.ProcessingTask = FakeProcessingTask

    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=15).run()
    source_language = next(item for item in at.selectbox if item.label == "源语言")
    target_language = next(item for item in at.selectbox if item.label == "目标语言")
    check("persisted EN→DE language pair initializes in the UI", (
        source_language.value == "en" and target_language.value == "de"
    ), {"source": source_language.value, "target": target_language.value})
    check("UI explains that processing follows the selected language pair", any(
        "自动随语言对调整" in item.value for item in at.caption
    ))

    at.file_uploader[0].upload(
        "multilingual.xlsx",
        source_io.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run()
    check("multilingual workbook renders without exception", not at.exception,
          [item.message for item in at.exception])

    source_column = next(item for item in at.selectbox if item.label == "待分析的英语源文本列")
    de_column = next(item for item in at.selectbox if item.label == "同行德语译文列（可选）")
    check("UI auto-maps EN-v2 and DE-MT", (
        source_column.value == 2 and de_column.value == 3
    ), {"source": source_column.value, "target": de_column.value})
    check("aligned target selector cannot reuse the source column", not any(
        option == source_column.value or str(option).startswith(f"[{source_column.value}]")
        for option in de_column.options
    ), de_column.options)
    check("common flow hides configuration behind one optional disclosure", (
        not any(item.label in {"源文件结构", "缺失译文处理"} for item in at.radio)
        and any("处理已就绪" in item.value and "同行双语" in item.value for item in at.success)
        and any(item.label == "查看或修改自动配置" for item in at.expander)
    ))
    check("UI reports deterministic language-header matching", any(
        "语言列名自动匹配" in item.value for item in at.caption
    ))

    de_column.set_value(-1).run()
    check("choosing no aligned target automatically switches to monolingual handling", (
        any("处理已就绪" in item.value and "单语" in item.value for item in at.success)
        and any("生成德语译文" in item.value for item in at.caption)
    ))

    target_language = next(item for item in at.selectbox if item.label == "目标语言")
    target_language.set_value("fr").run()
    fr_column = next(item for item in at.selectbox if item.label == "同行法语译文列（可选）")
    check("switching the target language remaps the aligned target column", fr_column.value == 4, fr_column.value)

    existing_only = next(
        item for item in at.checkbox
        if item.label == "只沿用现有译文（不调用 AI 翻译）"
    )
    existing_only.set_value(True).run()
    check("AI opt-out remains available as an advanced edge-case option", (
        next(
            item for item in at.checkbox
            if item.label == "只沿用现有译文（不调用 AI 翻译）"
        ).value
        and any("无法可靠对应的术语译文留空" in item.value for item in at.caption)
    ))

    start = next(button for button in at.button if button.label == "开始提取术语")
    check("valid EN→FR input enables extraction", not start.disabled)
    start.click().run()
    check("processing task receives the selected language pair and columns", (
        len(FakeProcessingTask.calls) == 1
        and FakeProcessingTask.calls[0]["source_language"] == "en"
        and FakeProcessingTask.calls[0]["target_language"] == "fr"
        and FakeProcessingTask.calls[0]["bilingual"]
        and FakeProcessingTask.calls[0]["no_translate"]
        and FakeProcessingTask.calls[0]["kwargs"]["src_col"] == 2
        and FakeProcessingTask.calls[0]["kwargs"]["src_en_col"] == 4
        and FakeProcessingTask.calls[0]["kwargs"]["key_col"] == 0
    ), FakeProcessingTask.calls)

    if FakeProcessingTask.calls:
        call = FakeProcessingTask.calls[0]
        empty_glossary = pd.read_excel(call["glossary_path"])
        check("source-only EN→FR run creates pair-specific empty glossary headers", (
            empty_glossary.empty
            and list(empty_glossary.columns) == ["英语术语", "法语译文"]
        ), list(empty_glossary.columns))
        shutil.rmtree(Path(call["source_path"]).parent, ignore_errors=True)

backend.ProcessingTask = original_task
if previous_api_key is None:
    os.environ.pop("OPENAI_API_KEY", None)
else:
    os.environ["OPENAI_API_KEY"] = previous_api_key

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
