#!/usr/bin/env python3
"""Build a clean, distributable package (allowlist-based).

Produces dist/<NAME>/ staging tree + dist/<NAME>.zip containing only what an
end user needs to run the tool on a fresh Windows machine:
  - runtime code (core/, ui/, cli.py, config*.py)
  - assets, profiles, .streamlit theme
  - bundled annotation tool (standalone HTML) + its user guide
  - user-facing docs + launchers (run.bat / setup.bat)

Excludes: .git, __pycache__, output/, database/, runtime UI state json,
dev/test harness (scripts/, chain_test/, test_file/, annotation/test/),
internal analysis docs, and anything carrying a key.

Zip stores names as UTF-8 (flag 0x800) so Chinese filenames survive Windows unzip.
"""
import os
import sys
import shutil
import zipfile
import py_compile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Game-Terms-Extraction-Langlobal"
DIST = ROOT / "dist"
STAGE = DIST / NAME
ZIP = DIST / f"{NAME}.zip"

# Files copied verbatim (path relative to project root)
FILES = [
    "cli.py",
    "config.py",
    "config_template.py",
    "header_cache.json",
    "requirements.txt",
    "run.bat",
    "setup.bat",
    "README.md",
    "README_ZH.md",
    "LICENSE",
    ".streamlit/config.toml",
    "assets/logo.jpeg",
    "annotation/术语标注助手_v3.0.html",
    "annotation/README.md",
    "annotation/docs/操作指南_术语标注助手.html",
    "docs/PM使用指南_游戏术语提取与标注.html",
    "docs/ARCHITECTURE.md",
    "docs/CHANGELOG.md",
    "docs/Profile撰写指南.html",
]

# Whole directories (recursively), pruned of junk below
DIRS = ["core", "ui", "profiles"]

PRUNE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
PRUNE_SUFFIX = {".pyc", ".pyo", ".swp", ".swo"}
PRUNE_NAMES = {".DS_Store"}

SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
)


def keep(path: Path) -> bool:
    if path.name in PRUNE_NAMES:
        return False
    if path.suffix in PRUNE_SUFFIX:
        return False
    if any(part in PRUNE_DIRS for part in path.parts):
        return False
    return True


def copy_file(rel: str):
    src = ROOT / rel
    if not src.exists():
        print(f"  MISSING (skipped): {rel}")
        return 0
    dst = STAGE / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def copy_dir(rel: str):
    src = ROOT / rel
    n = 0
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        if not keep(p):
            continue
        relp = p.relative_to(ROOT)
        dst = STAGE / relp
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        n += 1
    return n


def scan_secrets(root: Path) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            hits.append(str(path.relative_to(root)))
    return hits


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    STAGE.mkdir(parents=True)

    print("== Staging files ==")
    nf = sum(copy_file(f) for f in FILES)
    print(f"  files: {nf}/{len(FILES)}")
    print("== Staging dirs ==")
    for d in DIRS:
        c = copy_dir(d)
        print(f"  {d}/: {c} files")

    # Windows launchers MUST use CRLF — cmd.exe misparses LF-only .bat
    # (drops leading chars of tokens, breaks goto/labels). Normalize in the package.
    print("== Normalizing .bat to CRLF ==")
    for p in STAGE.rglob("*.bat"):
        raw = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        p.write_bytes(raw)
        print(f"  {p.relative_to(STAGE)}")

    print("== Secret scan ==")
    secret_hits = scan_secrets(STAGE)
    if secret_hits:
        for path in secret_hits:
            print(f"  FAIL possible credential: {path}")
        print("  Package aborted; remove or externalize credentials first.")
        return 1
    print("  no credential patterns found")

    # Static verification: every staged .py must compile
    print("== Compile check ==")
    bad = 0
    for p in STAGE.rglob("*.py"):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            bad += 1
            print(f"  FAIL {p.relative_to(STAGE)}: {e}")
    # py_compile leaves __pycache__; strip it before zipping
    for pc in STAGE.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    if bad:
        print(f"  {bad} file(s) failed to compile — aborting.")
        return 1
    print("  all .py compile OK")

    # Zip with UTF-8 names, rooted under NAME/ for clean extraction
    print("== Zipping ==")
    total = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(STAGE.rglob("*")):
            if p.is_dir():
                continue
            arc = f"{NAME}/{p.relative_to(STAGE).as_posix()}"
            zi = zipfile.ZipInfo(arc)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= 0x800  # explicit UTF-8 filename flag
            zi.external_attr = 0o644 << 16
            z.writestr(zi, p.read_bytes())
            total += 1

    size = ZIP.stat().st_size
    print(f"  {total} entries -> {ZIP.relative_to(ROOT)}  ({size/1024:.0f} KB)")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
