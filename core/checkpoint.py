import json, hashlib, shutil, time
from pathlib import Path

from core.languages import DEFAULT_SOURCE_LANGUAGE, DEFAULT_TARGET_LANGUAGE, validate_language_pair
from core.request_cache import write_json


def checkpoint_dir_name(
    file_bytes: bytes,
    profile: str,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> str:
    h = hashlib.md5(file_bytes).hexdigest()[:8]
    source_language, target_language = validate_language_pair(source_language, target_language)
    if (source_language, target_language) != (DEFAULT_SOURCE_LANGUAGE, DEFAULT_TARGET_LANGUAGE):
        return f"src_{profile}_{source_language}-{target_language}_{h}"
    return f"src_{profile}_{h}"


def load(checkpoint_dir: str) -> dict:
    ckpt_file = Path(checkpoint_dir) / "checkpoint.json"
    if not ckpt_file.exists():
        return {"chunk_idx": 0, "terms": [], "total_chunks": 0}
    for _ in range(3):
        try:
            return json.loads(ckpt_file.read_text(encoding="utf-8"))
        except (PermissionError, OSError):
            time.sleep(0.5)
    return {"chunk_idx": 0, "terms": [], "total_chunks": 0}


def save(checkpoint_dir: str, chunk_idx: int, terms: list, total_chunks: int, **state):
    write_json(Path(checkpoint_dir) / "checkpoint.json", {
        **state, "chunk_idx": chunk_idx, "terms": terms, "total_chunks": total_chunks,
    })


def clear(checkpoint_dir: str):
    p = Path(checkpoint_dir)
    if p.exists():
        shutil.rmtree(p)


def save_meta(checkpoint_dir: str, data: dict):
    write_json(Path(checkpoint_dir) / "run_meta.json", data)


def load_meta(checkpoint_dir: str) -> dict:
    p = Path(checkpoint_dir) / "run_meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_inputs(checkpoint_dir: str, source_path: str, glossary_path: str) -> tuple:
    """Copy source and glossary into the checkpoint dir for resumption.

    Called only after configuration validation. Source and glossary snapshots
    reflect the validated inputs; never copy a snapshot onto itself.
    Returns (src_path, gl_path) pointing to the checkpoint copies.
    """
    d = Path(checkpoint_dir)
    d.mkdir(parents=True, exist_ok=True)
    src_dst = d / "source.xlsx"
    gl_dst = d / "glossary.xlsx"
    if src_dst.resolve() != Path(source_path).resolve():
        shutil.copy2(source_path, src_dst)
    if gl_dst.resolve() != Path(glossary_path).resolve():
        shutil.copy2(glossary_path, gl_dst)
    return str(src_dst), str(gl_dst)
