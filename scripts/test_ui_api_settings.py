#!/usr/bin/env python3
"""Streamlit checks for configuring every API service without persisting secrets."""

import json
import os
import sys
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ui.ui_backend as backend


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


previous_main_key = os.environ.get("OPENAI_API_KEY")
os.environ["OPENAI_API_KEY"] = "ui-main-secret"
original_test_api_connection = backend.test_api_connection
backend.test_api_connection = lambda _cfg, service="main": (True, f"{service} 连接测试通过")

with tempfile.TemporaryDirectory() as tmp:
    backend.CONFIG_FILE = Path(tmp) / "config.json"
    backend.MODELS_FILE = Path(tmp) / "models.json"
    backend.CONFIG_FILE.write_text(json.dumps({
        "model": "gemini-3.1-flash-lite",
        "profile": "yanyun",
        "api_base": "https://example.invalid/v1",
    }), encoding="utf-8")

    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=15).run()
    at.radio[0].set_value("设置").run()

    inheritance = [item for item in at.checkbox if item.label == "密钥和地址全部沿用主生成服务"]
    ck("settings page exposes inheritance controls for all auxiliary services",
       len(inheritance) == 3, len(inheritance))

    for item in inheritance:
        item.set_value(False)
    at.run()

    labels = [item.label for item in at.text_input]
    expected_key_labels = {
        "API 密钥",
        "人名/地名预识别（NER） API 密钥",
        "术语相似度匹配（Embedding） API 密钥",
        "表头识别 API 密钥",
    }
    ck("settings page exposes all four API key inputs",
       expected_key_labels <= set(labels), labels)
    ck("settings page exposes independent Base URLs", {
        "API 服务地址（Base URL）",
        "人名/地名预识别（NER）服务地址（Base URL）",
        "术语相似度匹配（Embedding）服务地址（Base URL）",
        "表头识别服务地址（Base URL）",
    } <= set(labels), labels)
    ck("settings page exposes dedicated auxiliary models", {
        "NER 模型 ID", "Embedding 模型 ID", "表头识别模型 ID",
    } <= set(labels), labels)

    secrets = {
        "API 密钥": "ui-main-secret",
        "人名/地名预识别（NER） API 密钥": "ui-ner-secret",
        "术语相似度匹配（Embedding） API 密钥": "ui-embedding-secret",
        "表头识别 API 密钥": "ui-detect-secret",
    }
    for item in at.text_input:
        if item.label in secrets:
            item.set_value(secrets[item.label])
    at.run()

    saved = backend.CONFIG_FILE.read_text(encoding="utf-8")
    ck("UI auto-save never writes API key values", not any(value in saved for value in secrets.values()), saved)
    saved_fields = json.loads(saved)
    ck("UI auto-save contains no key/token/secret fields", not any(
        "key" in name.lower() or "token" in name.lower() or "secret" in name.lower()
        for name in saved_fields
    ), saved_fields)
    ck("settings explain that API keys are memory-only", any(
        "API 密钥仅保留在当前进程内存" in item.value for item in at.caption
    ))

    main_test = next(item for item in at.button if item.label == "测试主生成服务连接")
    main_test.click().run()
    ck("connection test result is shown for the tested configuration", any(
        "main 连接测试通过" in item.value for item in at.success
    ))
    stored_result = at.session_state["api_test_result"]
    ck("connection test state stores only a hash signature", isinstance(stored_result, dict)
       and len(stored_result.get("signature", "")) == 64
       and "ui-main-secret" not in str(stored_result), stored_result)

    main_base = next(item for item in at.text_input if item.label == "API 服务地址（Base URL）")
    main_base.set_value("https://changed.example.invalid/v1").run()
    ck("changing API configuration hides the stale success result", not any(
        "main 连接测试通过" in item.value for item in at.success
    ))

if previous_main_key is None:
    os.environ.pop("OPENAI_API_KEY", None)
else:
    os.environ["OPENAI_API_KEY"] = previous_main_key
backend.test_api_connection = original_test_api_connection


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
