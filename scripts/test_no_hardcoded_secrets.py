#!/usr/bin/env python3
"""Fail when source files or distributable archives contain credential patterns."""

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKIP_DIRS = {".git", "__pycache__", "output", "database", "test_file", "node_modules"}
PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
)


def contains_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in PATTERNS)


hits = []
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
        continue
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if contains_secret(archive.read(name)):
                        hits.append(f"{path.relative_to(ROOT)}::{name}")
        except zipfile.BadZipFile:
            pass
    elif contains_secret(path.read_bytes()):
        hits.append(str(path.relative_to(ROOT)))

ui_config = ROOT / ".ui_config.json"
secret_fields = []
if ui_config.exists():
    try:
        data = json.loads(ui_config.read_text(encoding="utf-8"))
        secret_fields = [name for name in data
                         if "key" in name.lower() or "token" in name.lower() or "secret" in name.lower()]
    except Exception as error:
        hits.append(f".ui_config.json unreadable: {type(error).__name__}")

source_text = "\n".join(
    path.read_text(encoding="utf-8", errors="ignore")
    for path in ROOT.rglob("*.py")
    if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
)
for forbidden in ("_BUNDLED_" + "DETECT_KEY", "DEFAULT_" + "API_KEY"):
    if forbidden in source_text:
        hits.append(f"forbidden source symbol: {forbidden}")

if hits or secret_fields:
    for hit in hits:
        print(f"FAIL | possible credential: {hit}")
    for field in secret_fields:
        print(f"FAIL | persisted secret field: {field}")
    raise SystemExit(1)

print("PASS | no hardcoded credential patterns in source or distribution archives")
print("PASS | persisted UI config contains no secret fields")
