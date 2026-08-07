#!/usr/bin/env python3
"""Offline checks for Streamlit UI model management."""

import json
import sys
import tempfile
from pathlib import Path
import yaml

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

    cfg = backend.RunConfig(model="gemini-3.1-flash-lite", profile="yanyun")

    added = backend.add_model_and_select(cfg, "  custom-model-x  ")
    ck("custom model add returns stripped name", added == "custom-model-x", added)
    ck("custom model appears in list", "custom-model-x" in backend.get_all_models(), backend.get_all_models())
    ck("config switches to new model", cfg.model == "custom-model-x", cfg.model)
    saved = json.loads(backend.CONFIG_FILE.read_text(encoding="utf-8"))
    ck("selected model persists to config file", saved["model"] == "custom-model-x", saved)

    before = backend.MODELS_FILE.read_text(encoding="utf-8")
    empty = backend.add_model_and_select(cfg, "   ")
    after = backend.MODELS_FILE.read_text(encoding="utf-8")
    ck("blank model name is ignored", empty == "" and before == after and cfg.model == "custom-model-x")

    preset = backend.PRESET_MODELS[0]
    backend.remove_model(preset)
    ck("preset can be hidden before re-add", preset not in backend.get_all_models(), backend.get_all_models())
    readded = backend.add_model_and_select(cfg, preset)
    ck("re-adding hidden preset returns preset name", readded == preset, readded)
    ck("re-adding hidden preset restores list entry", preset in backend.get_all_models(), backend.get_all_models())
    ck("re-adding hidden preset selects it", cfg.model == preset, cfg.model)

    template = yaml.safe_load(backend.get_profile_template())
    examples = template.get("extract_examples", {})
    ck("profile template keeps both include and exclude examples",
       set(examples) == {"include", "exclude"}, examples)


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
