"""Validated boundaries for OpenAI-compatible chat responses."""

from core.json_util import extract_json


class ResponseValidationError(ValueError):
    pass


class PipelineIncompleteError(RuntimeError):
    def __init__(self, stage: str, failures: list[str]):
        self.stage = stage
        self.failures = failures
        super().__init__(f"{stage}未完成（{len(failures)} 项），断点已保留：" + "; ".join(failures[:5]))


def chat_content(response) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise ResponseValidationError("服务未返回有效的 choices")
    choice = choices[0]
    finish = getattr(choice, "finish_reason", None)
    if finish not in (None, "stop"):
        raise ResponseValidationError(f"响应未正常结束：{finish}")
    content = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ResponseValidationError("服务返回空内容或非文本内容")
    return content


def response_usage(response) -> dict:
    usage = getattr(response, "usage", None)
    return {key: getattr(usage, key, 0) or 0 for key in (
        "total_tokens", "prompt_tokens", "completion_tokens",
    )}


def json_object(content: str) -> dict:
    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        raise ResponseValidationError("服务未返回有效 JSON 对象")
    return parsed


def validate_terms(parsed: dict) -> dict:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("terms"), list):
        raise ResponseValidationError("提取响应必须包含 terms 数组")
    for item in parsed["terms"]:
        if not isinstance(item, dict):
            raise ResponseValidationError("terms 条目必须为对象")
        term = item.get("term") or item.get("source_term") or item.get("zh_term")
        if not isinstance(term, str) or not term.strip():
            raise ResponseValidationError("terms 条目缺少非空源术语")
        for key in ("category", "target_term", "eng_term", "row_id"):
            if key in item and not isinstance(item[key], str):
                raise ResponseValidationError(f"terms.{key} 必须为文本")
    return parsed


def parse_translations(content: str) -> dict[str, str]:
    parsed = json_object(content)
    if not isinstance(parsed.get("translations"), list):
        raise ResponseValidationError("翻译响应必须包含 translations 数组")
    result = {}
    for item in parsed["translations"]:
        if not isinstance(item, dict) or not isinstance(item.get("term"), str):
            raise ResponseValidationError("翻译条目缺少源术语")
        term, translation = item["term"].strip(), item.get("translation")
        if not term or not isinstance(translation, str):
            raise ResponseValidationError("翻译条目的 term/translation 类型无效")
        if term in result and result[term] != translation.strip():
            raise ResponseValidationError("同一响应对同一术语返回冲突译文")
        if translation.strip():
            result[term] = translation.strip()
    return result


def parse_entities(content: str) -> dict[str, list[str]]:
    parsed = json_object(content)
    for key in ("persons", "places"):
        values = parsed.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ResponseValidationError(f"NER 响应必须包含文本数组 {key}")
    return parsed
