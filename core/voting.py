"""Deterministic vote aggregation over stable source rows."""

from collections import defaultdict
from core.responses import PipelineIncompleteError, ResponseValidationError, validate_terms
from core.source_records import select_record


def ensure_complete(raw_votes, expected_chunks):
    failures = []
    if len(raw_votes) != 3:
        failures.append("缺少投票轮次")
    for vote, responses in enumerate(raw_votes):
        if len(responses) != expected_chunks:
            failures.append(f"v{vote}: 响应数量不完整")
        for response in responses:
            label = response.get("custom_id", f"v{vote}")
            if response.get("error"):
                failures.append(f"{label}: {response['error']}")
            else:
                try:
                    validate_terms(response.get("extracted_terms"))
                except ResponseValidationError as exc:
                    failures.append(f"{label}: {exc}")
        identifiers = [r.get("source_file") for r in responses if r.get("source_file")]
        if len(identifiers) != len(set(identifiers)):
            failures.append(f"v{vote}: 重复 chunk 响应")
    if failures:
        raise PipelineIncompleteError("提取", failures)


def collect_llm_votes(raw_votes, records_by_chunk=None):
    votes = []
    for vote_id, responses in enumerate(raw_votes):
        for i, response in enumerate(responses):
            for item in validate_terms(response.get("extracted_terms"))["terms"]:
                candidate = {
                    "term": (item.get("term") or item.get("source_term") or item.get("zh_term")).strip(),
                    "category": item.get("category", "").strip(),
                    "target_term": (item.get("target_term") or item.get("eng_term") or "").strip(),
                    "_vote": vote_id,
                }
                if item.get("row_id"):
                    candidate["row_id"] = item["row_id"]
                if records_by_chunk:
                    if not item.get("row_id"):
                        raise ResponseValidationError("提取响应缺少 row_id，无法核对来源行")
                    records = records_by_chunk.get(response.get("source_file"))
                    if records is None and response.get("source_file"):
                        raise ResponseValidationError("响应的 chunk 标识不属于本批请求")
                    if records is None:
                        records = list(records_by_chunk.values())[i]
                    record = select_record(candidate, records)
                    if item.get("row_id") and record is None:
                        raise ResponseValidationError(f"{response.get('custom_id', '')}: 术语未命中指定来源行")
                    if record:
                        candidate["row_id"] = record.row_id
                    else:
                        candidate["target_term"] = ""
                votes.append(candidate)
    return votes


def pick_category(counts, allowed):
    valid = {category: count for category, count in counts.items() if category in allowed}
    return max((valid or counts), key=lambda c: ((valid or counts)[c], c), default="")


def count_llm_votes(votes, allowed_categories=None):
    groups = defaultdict(list)
    for vote in votes:
        groups[(vote["term"].casefold(), vote.get("row_id", ""))].append(vote)
    result = []
    for _, items in sorted(groups.items()):
        supported = {item["_vote"] for item in items}
        if len(supported) < 3:
            continue
        categories, targets = defaultdict(set), defaultdict(set)
        for item in items:
            if item.get("category"):
                categories[item["category"]].add(item["_vote"])
            if item.get("target_term"):
                targets[item["target_term"]].add(item["_vote"])
        out = {
            "term": min(item["term"] for item in items),
            "category": pick_category({c: len(v) for c, v in categories.items()}, set(allowed_categories or [])),
        }
        if targets:
            out["target_term"] = max(targets, key=lambda t: (len(targets[t]), t))
        if items[0].get("row_id"):
            out["row_id"] = items[0]["row_id"]
            out["vote_count"] = len(supported)
        if len(targets) > 1:
            out["note"] = "投票返回不同译文，待核：" + " / ".join(sorted(targets))
        result.append(out)
    return result


def consolidate_terms(terms):
    merged = {}
    by_name = defaultdict(set)
    for term in terms:
        key = (term["term"].casefold(), term.get("category", ""), term.get("target_term", "").casefold())
        by_name[key[0]].add(key)
        if key not in merged:
            merged[key] = dict(term)
        else:
            previous = merged[key].get("source_occurrences", [])
            known = {item["row_id"] for item in previous}
            occurrences = previous + [item for item in term.get("source_occurrences", []) if item["row_id"] not in known]
            notes = "; ".join(dict.fromkeys(filter(None, [merged[key].get("note"), term.get("note")])))
            if term.get("source_is_exact") and not merged[key].get("source_is_exact"):
                merged[key] = dict(term)
            merged[key]["source_occurrences"] = occurrences
            if notes:
                merged[key]["note"] = notes
    for key, term in merged.items():
        if len(by_name[key[0]]) > 1:
            term["note"] = "; ".join(filter(None, [term.get("note"), "同形术语存在分类或译文冲突，分项保留待核"]))
    return list(merged.values())
