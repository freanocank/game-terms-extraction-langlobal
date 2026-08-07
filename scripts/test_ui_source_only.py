#!/usr/bin/env python3
"""Streamlit regression checks for source-only extraction."""

import json
import logging
import os
import shutil
import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit.elements.lib.policies as widget_policies
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import core.header_detect as header_detect
import ui.ui_backend as backend


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


class FakeProcessingTask:
    calls = []

    def __init__(self):
        self.done = False
        self.error = None
        self.results = []
        self.info = ""
        self.output_dir = ""
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
            "kwargs": kwargs,
        })

    def cancel(self):
        self._cancelling = True


source_io = BytesIO()
pd.DataFrame({
    "文本 ID": ["L001", "L002"],
    "中文原文": ["青长老抵达墨门。", "获得浮云匙。"],
}).to_excel(source_io, index=False)

previous_api_key = os.environ.get("OPENAI_API_KEY")
os.environ["OPENAI_API_KEY"] = "test-key"
original_detect_source = header_detect.detect_source_column
widget_messages = []


class WidgetWarningCapture(logging.Handler):
    def emit(self, record):
        widget_messages.append(record.getMessage())


widget_handler = WidgetWarningCapture()
logging.getLogger("streamlit.elements.lib.policies").addHandler(widget_handler)
widget_policies._shown_default_value_warning = False


def recoverable_detection_failure(*args, **kwargs):
    return {
        "text_col": 1,
        "key_col": None,
        "en_col": None,
        "method": "default",
        "confidence": "低（默认预选，请人工确认）",
        "fallback_code": "connection_failed",
        "fallback_reason": "无法连接表头识别服务",
    }


header_detect.detect_source_column = recoverable_detection_failure

with tempfile.TemporaryDirectory() as tmp:
    backend.CONFIG_FILE = Path(tmp) / "config.json"
    backend.MODELS_FILE = Path(tmp) / "models.json"
    backend.CONFIG_FILE.write_text(json.dumps({
        "model": "gemini-3.1-flash-lite",
        "profile": "yanyun",
        "api_base": "https://example.invalid/v1",
    }), encoding="utf-8")
    backend.ProcessingTask = FakeProcessingTask
    correction_calls = []

    def capture_source_correction(*args, **kwargs):
        correction_calls.append((args, kwargs))

    header_detect.record_source_correction = capture_source_correction
    header_detect.record_glossary_correction = lambda *args, **kwargs: None

    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=15).run()
    start = next(button for button in at.button if button.label == "开始提取术语")
    ck("start is disabled before source upload", start.disabled)

    at.file_uploader[0].upload(
        "source_only.xlsx",
        source_io.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run()

    ck("source-only upload renders without exception", not at.exception,
       [item.message for item in at.exception])
    warnings = [item.value for item in at.warning]
    ck("AI fallback reason is visible but non-blocking", any(
        "AI 表头识别未完成" in value and "无法连接表头识别服务" in value
        for value in warnings
    ), warnings)
    ck("fallback offers an explicit retry action", any(
        button.label == "重新尝试 AI 表头识别" for button in at.button
    ))
    ck("column widgets do not duplicate defaults and Session State", not any(
        "created with a default value" in message for message in widget_messages
    ), widget_messages)
    labels = [item.label for item in at.selectbox]
    ck("source column mapping is available", "待分析的中文源文本列" in labels, labels)
    ck("optional text ID mapping is available", "文本 ID 列（可选）" in labels, labels)

    source_mapping = next(item for item in at.selectbox if item.label == "待分析的中文源文本列")
    source_mapping.set_value(0).run()
    retry = next(button for button in at.button if button.label == "重新尝试 AI 表头识别")
    retry.click().run()
    source_mapping = next(item for item in at.selectbox if item.label == "待分析的中文源文本列")
    ck("failed retry preserves the user's manual column choice", source_mapping.value == 0,
       source_mapping.value)
    warnings = [item.value for item in at.warning]
    ck("retry feedback says the manual choice was preserved", any(
        "已保留你的手动选择" in value for value in warnings
    ), warnings)

    start = next(button for button in at.button if button.label == "开始提取术语")
    ck("source-only upload enables extraction", not start.disabled)
    ck("UI explains optional glossary behavior", any(
        "未上传参考术语表" in item.value for item in at.info
    ))

    start.click().run()
    ck("source-only start reaches processing task", len(FakeProcessingTask.calls) == 1,
       FakeProcessingTask.calls)
    ck("manual correction is recorded with v2 cache context", (
        len(correction_calls) == 1
        and isinstance(correction_calls[0][1].get("df"), pd.DataFrame)
        and bool(correction_calls[0][1].get("base_url"))
        and bool(correction_calls[0][1].get("model"))
    ), correction_calls)
    if FakeProcessingTask.calls:
        call = FakeProcessingTask.calls[0]
        glossary_path = Path(call["glossary_path"])
        glossary_df = pd.read_excel(glossary_path)
        ck("generated glossary is a valid empty workbook", glossary_df.empty,
           glossary_df.to_dict("records"))
        ck("generated glossary keeps the two-column pipeline contract",
           list(glossary_df.columns) == ["中文术语", "英文译文"], list(glossary_df.columns))
        ck("source-only run resets glossary column mapping to the empty workbook",
           call["kwargs"]["gl_cn_col"] == 0 and call["kwargs"]["gl_en_col"] == 1,
           call["kwargs"])
        shutil.rmtree(Path(call["source_path"]).parent, ignore_errors=True)

header_detect.detect_source_column = original_detect_source
logging.getLogger("streamlit.elements.lib.policies").removeHandler(widget_handler)

if previous_api_key is None:
    os.environ.pop("OPENAI_API_KEY", None)
else:
    os.environ["OPENAI_API_KEY"] = previous_api_key


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
