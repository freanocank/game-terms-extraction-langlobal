from __future__ import annotations

import re


DEFAULT_SOURCE_LANGUAGE = "zh-Hans"
DEFAULT_TARGET_LANGUAGE = "en"

LANGUAGE_NAMES = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁体中文",
    "en": "英语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "it": "意大利语",
    "pt-BR": "巴西葡萄牙语",
    "pt-PT": "欧洲葡萄牙语",
    "ja": "日语",
    "ko": "韩语",
    "ru": "俄语",
    "ar": "阿拉伯语",
    "tr": "土耳其语",
    "pl": "波兰语",
    "nl": "荷兰语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "uk": "乌克兰语",
    "cs": "捷克语",
    "hu": "匈牙利语",
    "sv": "瑞典语",
    "da": "丹麦语",
    "no": "挪威语",
    "fi": "芬兰语",
}

COMMON_LANGUAGE_CODES = tuple(LANGUAGE_NAMES)

_ALIASES = {
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "cn": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "tc": "zh-Hant",
    "zh-hant": "zh-Hant",
    "en-us": "en",
    "en-gb": "en",
    "de-de": "de",
    "fr-fr": "fr",
    "es-es": "es",
    "it-it": "it",
    "ja-jp": "ja",
    "jp": "ja",
    "ko-kr": "ko",
    "kr": "ko",
    "pt-br": "pt-BR",
    "pt-pt": "pt-PT",
}

_HEADER_ALIASES = {
    "zh-Hans": {"cn", "zh", "zh-cn", "zh-hans", "中文", "简中", "简体中文", "中文原文", "中文术语"},
    "zh-Hant": {"tc", "zh-tw", "zh-hant", "繁中", "繁体中文", "繁體中文"},
    "en": {"en", "en-v2", "english", "英文", "英语", "英語", "英文原文", "英文译文", "英文譯文"},
    "de": {"de", "de-mt", "german", "deutsch", "德语", "德語"},
    "fr": {"fr", "fr-mt", "french", "français", "francais", "法语", "法語"},
    "es": {"es", "es-mt", "spanish", "español", "espanol", "西班牙语", "西班牙語"},
    "it": {"it", "it-mt", "italian", "italiano", "意大利语", "義大利語"},
    "pt-BR": {"pt-br", "ptbr", "brazilian portuguese", "巴西葡萄牙语"},
    "pt-PT": {"pt-pt", "pt", "portuguese", "português", "portugues", "葡萄牙语"},
    "ja": {"ja", "jp", "ja-jp", "japanese", "日本語", "日语", "日語"},
    "ko": {"ko", "kr", "ko-kr", "korean", "한국어", "韩语", "韓語"},
    "ru": {"ru", "ru-mt", "russian", "русский", "俄语", "俄語"},
    "ar": {"ar", "ar-mt", "arabic", "العربية", "阿拉伯语", "阿拉伯語"},
    "tr": {"tr", "tr-mt", "turkish", "türkçe", "turkce", "土耳其语", "土耳其語"},
    "pl": {"pl", "pl-mt", "polish", "polski", "波兰语", "波蘭語"},
    "nl": {"nl", "nl-mt", "dutch", "nederlands", "荷兰语", "荷蘭語"},
    "th": {"th", "th-mt", "thai", "ไทย", "泰语", "泰語"},
    "vi": {"vi", "vi-mt", "vietnamese", "tiếng việt", "tieng viet", "越南语", "越南語"},
    "id": {"id", "id-mt", "indonesian", "bahasa indonesia", "印尼语", "印尼語"},
    "uk": {"uk", "uk-mt", "ukrainian", "українська", "乌克兰语", "烏克蘭語"},
    "cs": {"cs", "cs-mt", "czech", "čeština", "cestina", "捷克语", "捷克語"},
    "hu": {"hu", "hu-mt", "hungarian", "magyar", "匈牙利语", "匈牙利語"},
    "sv": {"sv", "sv-mt", "swedish", "svenska", "瑞典语", "瑞典語"},
    "da": {"da", "da-mt", "danish", "dansk", "丹麦语", "丹麥語"},
    "no": {"no", "no-mt", "norwegian", "norsk", "挪威语", "挪威語"},
    "fi": {"fi", "fi-mt", "finnish", "suomi", "芬兰语", "芬蘭語"},
}

_BCP47_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def normalize_language_code(value: str) -> str:
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        raise ValueError("语言代码不能为空")
    alias = _ALIASES.get(raw.lower())
    if alias:
        return alias
    if not _BCP47_RE.fullmatch(raw):
        raise ValueError(f"无效的语言代码：{value}")
    parts = raw.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.title() if len(part) == 4 and part.isalpha() else part.upper())
    return "-".join(normalized)


def validate_language_pair(source_language: str, target_language: str) -> tuple[str, str]:
    source = normalize_language_code(source_language)
    target = normalize_language_code(target_language)
    if source == target:
        raise ValueError("源语言和目标语言不能相同")
    return source, target


def language_name(code: str) -> str:
    normalized = normalize_language_code(code)
    return LANGUAGE_NAMES.get(normalized, normalized)


def language_label(code: str) -> str:
    normalized = normalize_language_code(code)
    return f"{LANGUAGE_NAMES.get(normalized, normalized)}（{normalized}）"


def language_tag(code: str) -> str:
    return normalize_language_code(code).upper()


def is_chinese(code: str) -> bool:
    return normalize_language_code(code).startswith("zh-")


def language_header_aliases(code: str) -> set[str]:
    normalized = normalize_language_code(code)
    aliases = set(_HEADER_ALIASES.get(normalized, set()))
    aliases.update({normalized.lower(), normalized.split("-", 1)[0].lower()})
    return aliases
