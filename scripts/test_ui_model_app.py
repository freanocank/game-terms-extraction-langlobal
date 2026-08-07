#!/usr/bin/env python3
"""Streamlit AppTest checks for Settings model selection."""

import json
import sys
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import ui.ui_backend as backend

results = []


def ck(name, cond, detail=""):
    ok = bool(cond)
    results.append((name, ok))
    print(("PASS" if ok else "FAIL") + " | " + name + (("  >> " + str(detail)[:160]) if (detail and not ok) else ""))


with tempfile.TemporaryDirectory() as tmp:
    backend.MODELS_FILE = Path(tmp) / "models.json"
    backend.CONFIG_FILE = Path(tmp) / "config.json"
    backend.CONFIG_FILE.write_text(json.dumps({
        "api_key": "",
        "model": "gemini-3.1-flash-lite",
        "profile": "yanyun",
        "api_base": "https://api.vectorengine.ai/v1",
    }), encoding="utf-8")

    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=10)
    at.run()
    at.radio[0].set_value("设置").run()

    ck("model select initializes from persisted config", at.selectbox[0].value == "gemini-3.1-flash-lite", at.selectbox[0].value)

    add_input = next(i for i, item in enumerate(at.text_input) if item.label == "自定义模型 ID")
    at.text_input[add_input].set_value("ui-app-model-x").run()
    add_button = next(i for i, item in enumerate(at.button) if item.label == "添加并选用")
    try:
        at.button[add_button].click().run()
    except Exception as e:
        ck("add model click completes without widget state error", False, f"{type(e).__name__}: {e}")
    else:
        ck("add model click completes without widget state error", True)
        saved = json.loads(backend.CONFIG_FILE.read_text(encoding="utf-8"))
        ck("added model is selected in UI", at.selectbox[0].value == "ui-app-model-x", at.selectbox[0].value)
        ck("added model is persisted", saved["model"] == "ui-app-model-x", saved)
        ck("added model appears in backend list", "ui-app-model-x" in backend.get_all_models(), backend.get_all_models())


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
