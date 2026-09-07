"""Extraction-stage compatibility checks; never silently reuse legacy state."""

from pathlib import Path
from core.request_cache import fingerprint


def extraction_manifest(source_path, glossary, profile, opts, base_url, model):
    from config import EXTRACTOR_CONFIG, VOTE_TEMPS
    from core.source_records import resolved_columns

    root = Path(__file__).parent
    code = {name: fingerprint((root / name).read_text(encoding="utf-8")) for name in (
        "main.py", "prompt_base.py", "source_records.py", "voting.py", "ner.py", "llm_extractor.py",
    )}
    target_col, glossary_source, glossary_target = resolved_columns(opts)
    return {
        "version": 2,
        "source_hash": fingerprint(Path(source_path).read_bytes().hex()),
        "glossary_keys": sorted(glossary), "profile": profile,
        "languages": [opts.source_language, opts.target_language],
        "columns": [opts.src_col, target_col if opts.bilingual else None, opts.key_col, glossary_source, glossary_target],
        "bilingual": opts.bilingual, "chunk_chars": 1200, "chunk_overlap": 3,
        "provider": fingerprint(base_url.rstrip("/")), "model": model,
        "ner_provider": fingerprint((opts.ner_base_url or base_url).rstrip("/")),
        "ner_model": opts.ner_model or model,
        "max_tokens": opts.max_tokens or EXTRACTOR_CONFIG["max_tokens"],
        "vote_temperatures": VOTE_TEMPS, "code": code,
    }


def validate_resume(metadata, checkpoint, manifest):
    if checkpoint.get("chunk_idx", 0) or checkpoint.get("votes"):
        saved = metadata.get("extraction_manifest")
        if saved is None:
            raise ValueError("旧断点没有可验证的配置摘要，不能安全续跑；请使用新的断点目录，旧数据已保留")
        if fingerprint(saved) != fingerprint(manifest):
            raise ValueError("断点的输入、列映射、规则、语言对或模型已变化；请使用新的断点目录，旧数据已保留")
