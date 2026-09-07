"""Offline set-level scoring against a complete, reviewed gold set."""


def score_terms(results, gold):
    predicted = {item["term"].strip().casefold() for item in results if item["term"].strip()}
    expected = {item["term"].strip().casefold() for item in gold if item["term"].strip()}
    tp, fp, fn = predicted & expected, predicted - expected, expected - predicted
    precision = len(tp) / len(predicted) if predicted else 0.0
    recall = len(tp) / len(expected) if expected else 0.0
    report = {
        "predicted_count": len(predicted), "gold_count": len(expected),
        "tp": len(tp), "fp": len(fp), "fn": len(fn),
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "hits": sorted(tp), "extra_candidates": sorted(fp), "missed": sorted(fn),
    }
    translations = {}
    for item in gold:
        if item.get("translation"):
            translations.setdefault(item["term"].strip().casefold(), set()).add(item["translation"].strip())
    aligned = [item for item in results if item["term"].strip().casefold() in translations]
    correct = sum(str(item.get("translation") or "").strip() in translations[item["term"].strip().casefold()] for item in aligned)
    report["translation_evaluated"] = len(aligned)
    report["translation_correct"] = correct
    report["translation_accuracy"] = correct / len(aligned) if aligned else None
    return report


def replay_extraction(directory, source_path, glossary_path, profile_name, opts):
    import json
    import re
    from pathlib import Path
    import pandas as pd
    from core.main import load_config, load_glossary, match_and_translate, _filter_derived_terms, _in_glossary
    from core.languages import is_chinese
    from core.source_records import prepare_source_records, chunk_records, bind_context, resolved_columns
    from core.voting import ensure_complete, collect_llm_votes, count_llm_votes, consolidate_terms

    records = prepare_source_records(pd.read_excel(source_path), opts, Path(source_path).name)
    chunks = {f"c{i}": chunk for i, chunk in enumerate(chunk_records(records, 1200))}
    profile = load_config(profile_name)
    _, gl_source, gl_target = resolved_columns(opts)
    glossary, keys = load_glossary(glossary_path, gl_source, gl_target) if glossary_path else ({}, set())
    manifest_path = Path(directory) / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("回放缺少 manifest.json；旧版响应不能验证来源，请保留原数据并重新采集")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "version": 2, "chunks": ["\n\n".join(f"[{r.row_id}] {r.text}" for r in chunk) for chunk in chunks.values()],
        "profile": profile, "source_language": opts.source_language,
        "target_language": opts.target_language, "bilingual": opts.bilingual,
        "keys": [[r.row_id, r.key] for r in records], "glossary_keys": sorted(keys),
    }
    if manifest != expected:
        raise ValueError("回放来源、列映射、语言对或 Profile 与采集记录不一致")
    raw_votes = [[], [], []]
    for path in sorted(Path(directory).glob("b*_v*_r*.json")):
        match = re.fullmatch(r"b\d+_v([012])_r\d+\.json", path.name)
        if match:
            raw_votes[int(match[1])].append(json.loads(path.read_text(encoding="utf-8")))
    if not chunks:
        raise ValueError("回放源文件没有可分析文本")
    ensure_complete(raw_votes, len(chunks))
    for responses in raw_votes:
        if {response.get("source_file") for response in responses} != set(chunks):
            raise ValueError("回放响应不完整或与当前分块不一致；必须提供完整的三轮原始响应")
    terms = count_llm_votes(collect_llm_votes(raw_votes, chunks), profile.get("term_categories"))
    bind_context(terms, records)
    terms = consolidate_terms(terms)
    if is_chinese(opts.source_language):
        terms = _filter_derived_terms(terms, profile.get("address_suffixes", []), keys)
    excluded = set(profile.get("filterable_categories", []))
    terms = [term for term in terms if term.get("category") not in excluded or _in_glossary(term["term"], keys)]
    return match_and_translate(terms, glossary, profile, "", "", "", bilingual=opts.bilingual,
                               source_language=opts.source_language, target_language=opts.target_language,
                               translate_missing=False)
