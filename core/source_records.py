"""Row provenance and word-level alignment, independent of API orchestration."""

import re
from dataclasses import dataclass

import pandas as pd
from core.languages import DEFAULT_SOURCE_LANGUAGE, DEFAULT_TARGET_LANGUAGE, language_name, language_tag, validate_language_pair


_VAR_RE = re.compile(r'\$\{[^}]*\}|\$[a-zA-Z_]\w*|\$\d+|\{[^}]*\}|</?[a-zA-Z][^>]*>')


def clean_source(text: str) -> str:
    return _VAR_RE.sub('', text).strip()


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def raw_span(value: str, raw: str):
    removed = {i for match in _VAR_RE.finditer(raw) for i in range(*match.span())}
    offsets = [i for i in range(len(raw)) if i not in removed]
    cleaned = "".join(raw[i] for i in offsets)
    span = text_span(value, cleaned)
    return (offsets[span[0]], offsets[span[1] - 1] + 1) if span else None


def text_span(value: str, text: str):
    value = value.strip()
    if not value:
        return None
    pattern = r"\s*".join(re.escape(c) for c in value if not c.isspace())
    bounded = not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", value)
    for match in re.finditer(pattern, text, re.IGNORECASE):
        start, end = match.span()
        if bounded and (
            (start and (text[start - 1].isalnum() or text[start - 1] == "_") and value[0].isalnum())
            or (end < len(text) and (text[end].isalnum() or text[end] == "_") and value[-1].isalnum())
        ):
            continue
        return start, end
    return None


@dataclass(frozen=True)
class SourceRecord:
    row_id: str
    key: str
    source: str
    target: str
    text: str
    raw_source: str = ""
    raw_target: str = ""
    file: str = ""
    sheet: str = ""

    def occurrence(self, term: str) -> dict:
        return {
            "row_id": self.row_id, "key": self.key, "file": self.file, "sheet": self.sheet,
            "source": self.raw_source or self.source, "target": self.raw_target or self.target,
            "source_clean": self.source,
            "source_span": text_span(term, self.source),
            "raw_source_span": raw_span(term, self.raw_source or self.source),
        }


def resolved_columns(opts):
    return (
        opts.src_target_col if opts.src_target_col is not None else opts.src_en_col,
        opts.gl_source_col if opts.gl_source_col is not None else opts.gl_cn_col,
        opts.gl_target_col if opts.gl_target_col is not None else opts.gl_en_col,
    )


def prepare_source_records(df, opts, source_file="", sheet="") -> list[SourceRecord]:
    source_language, target_language = validate_language_pair(opts.source_language, opts.target_language)
    target_col, _, _ = resolved_columns(opts)
    count = df.shape[1]
    if not 0 <= opts.src_col < count:
        raise ValueError("源文本列索引超出文件范围")
    if opts.bilingual and (not 0 <= target_col < count or target_col == opts.src_col):
        raise ValueError("同行目标文本列索引无效或与源文本列重复")
    if opts.key_col is not None and (not 0 <= opts.key_col < count or opts.key_col == opts.src_col
                                     or (opts.bilingual and opts.key_col == target_col)):
        raise ValueError("Key 列索引无效或与文本列重复")

    def cell(value, key=False):
        if pd.isna(value):
            return ""
        return str(int(value)) if key and isinstance(value, float) and value.is_integer() else str(value)

    records = []
    for pos, row in enumerate(df.itertuples(index=False, name=None), 2):
        raw_source = cell(row[opts.src_col])
        raw_target = cell(row[target_col]) if opts.bilingual else ""
        source, target = clean_source(raw_source), clean_source(raw_target)
        if not re.search(r"\w", source):
            continue
        text = f"{language_tag(source_language)}: {source} | {language_tag(target_language)}: {target}" if opts.bilingual else source
        records.append(SourceRecord(
            f"row-{pos}", cell(row[opts.key_col], key=True) if opts.key_col is not None else "",
            source, target, text, raw_source, raw_target, source_file, sheet,
        ))
    return records


def unique_records(records):
    unique = {}
    for record in records:
        unique.setdefault(record.text, record)
    return list(unique.values())


def prepare_source_texts(df, opts):
    records = unique_records(prepare_source_records(df, opts))
    return [r.text for r in records], [r.key for r in records]


def records_from_texts(texts, keys=None, source_language=DEFAULT_SOURCE_LANGUAGE, target_language=DEFAULT_TARGET_LANGUAGE):
    marker = re.compile(rf"\s*\|\s*{re.escape(language_tag(target_language))}\s*:\s*", re.IGNORECASE)
    records = []
    for i, text in enumerate(texts):
        match = marker.search(text)
        source = re.sub(r"^[A-Za-z][A-Za-z0-9-]*\s*:\s*", "", text[:match.start()].strip(), count=1) if match else text
        target = text[match.end():] if match else ""
        records.append(SourceRecord(f"row-{i + 2}", keys[i] if keys else "", source, target, text))
    return records


def select_record(term, records):
    row_id = term.get("row_id") or term.get("source_row_id")
    candidates = [r for r in records if (not row_id or r.row_id == row_id) and text_span(term["term"], r.source)]
    if not candidates:
        return None
    target = term.get("target_term") or term.get("eng_term") or ""

    def rank(record):
        exact = normalized(record.source) == normalized(term["term"])
        named = bool(re.search(r"(?:姓名|名字|名称|角色)[：:\s]*" + re.escape(term["term"]), record.source))
        aligned = bool(target and text_span(target, record.target))
        return (not exact, not named, not aligned)

    return min(candidates, key=rank)


def bind_context(terms, records):
    for term in terms:
        record = select_record(term, records)
        term["source_text"] = record.text.replace("\n", " ").strip() if record else ""
        term["source_key"] = record.key if record else ""
        if record:
            term["source_row_id"] = record.row_id
            term["source_span"] = text_span(term["term"], record.source)
            term["source_raw"] = record.raw_source or record.source
            term["source_clean"] = record.source
            term["raw_source_span"] = raw_span(term["term"], term["source_raw"])
            term["source_is_exact"] = normalized(record.source) == normalized(term["term"])
            term["target_text"] = record.target
            term["target_raw"] = record.raw_target or record.target
            term["source_occurrences"] = [r.occurrence(term["term"]) for r in records if r.text == record.text]


def batch_match_context(terms, source_texts, source_keys=None, source_language=DEFAULT_SOURCE_LANGUAGE,
                        target_language=DEFAULT_TARGET_LANGUAGE):
    bind_context(terms, records_from_texts(source_texts, source_keys, source_language, target_language))


def aligned_target_value(term, bilingual, target_language=DEFAULT_TARGET_LANGUAGE):
    if not bilingual:
        return ""
    value = str(term.get("target_term") or term.get("eng_term") or "").strip()
    if not value:
        return ""
    source_text = str(term.get("source_text") or "")
    marker = re.search(rf"\s*\|\s*{re.escape(language_tag(target_language))}\s*:\s*", source_text, re.IGNORECASE)
    target = term.get("target_text") if marker else None
    if target is None and marker:
        target = source_text[marker.end():]
    reason = "无法核对" if target is None else "为空" if not target.strip() else "未命中目标单元格词边界"
    span = text_span(value, target) if target else None
    if span:
        term["target_span"] = span
        term["raw_target_span"] = raw_span(value, term.get("target_raw", target))
        return target[span[0]:span[1]]
    note = f"同行{language_name(target_language)}术语译文{reason}，未沿用"
    term["note"] = "; ".join(dict.fromkeys(filter(None, [term.get("note", ""), note])))
    return ""


def chunk_records(records, target_chars, overlap=3):
    chunks, current, size = [], [], 0
    for record in unique_records(records):
        if current and size + len(record.text) > target_chars:
            chunks.append(current)
            current = current[-overlap:] if overlap else []
            size = sum(len(r.text) + len(r.row_id) + 5 for r in current)
        current = [*current, record]
        size += len(record.text) + len(record.row_id) + 5
    if current:
        chunks.append(current)
    return chunks
