#!/usr/bin/env python3

import json
from typing import Dict, Any

from core.languages import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    language_name,
    language_tag,
    normalize_language_code,
    validate_language_pair,
)


def build_system_prompt(profile: Dict[str, Any], source_language: str = DEFAULT_SOURCE_LANGUAGE) -> str:
    source = normalize_language_code(source_language)
    game_type = profile.get("game_type", "游戏")
    if source == DEFAULT_SOURCE_LANGUAGE:
        principle = profile.get("core_principle", "")
        ex = profile.get("extract_examples", {})
    else:
        principle = (profile.get("core_principle_by_source_language") or {}).get(
            source,
            "识别在游戏项目中需要跨文本保持一致翻译的专有名称、系统名称和核心概念。",
        )
        ex = (profile.get("extract_examples_by_source_language") or {}).get(source, {})
    include_lines = "\n".join(ex.get("include", []))
    exclude_lines = "\n".join(ex.get("exclude", []))
    if source != DEFAULT_SOURCE_LANGUAGE and not include_lines:
        include_lines = "角色名、地名、组织名、物品名、技能名、任务名、UI 与玩法系统专名"

    exclude_section = f"\n\n❌ 不提取：\n{exclude_lines}" if exclude_lines else ""

    prompt = f"""\
你是一位专业的{game_type}游戏本地化术语抽取专家。你的任务是从游戏文本中识别并提取需要本地化处理的专业术语。

{principle}

【提取范围】

✅ 提取：
{include_lines}{exclude_section}

请严格按照要求的 JSON 格式输出，不要添加任何额外文字。"""
    if source != DEFAULT_SOURCE_LANGUAGE:
        prompt += f"\n\n当前待分析文本的源语言是{language_name(source)}（{source}）。只提取该源语言中的术语。"
    return prompt


def _build_rules(
    profile: Dict[str, Any],
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
) -> str:
    cats = "、".join(profile.get("term_categories", []))
    lead = (f"只提取以下类型的游戏专有名词：{cats}" if cats
            else "提取游戏专有名词：人名、地名、物品名、技能名、机构名、事件名、称谓")
    source = normalize_language_code(source_language)
    if source == DEFAULT_SOURCE_LANGUAGE:
        notes = profile.get("extraction_notes", [])
    else:
        notes = (profile.get("extraction_notes_by_source_language") or {}).get(source, [])
    rules = [lead] + notes
    return "提取规则：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))


def _build_ner_hint(ner_hints: dict) -> str:
    if not ner_hints:
        return ""
    persons = ner_hints.get("persons", [])
    places = ner_hints.get("places", [])
    if not persons and not places:
        return ""
    lines = "【预识别命名实体】以下人名和地名已确认出现在上述文本中，必须逐一检查并提炼为术语（请按上方给定的术语分类归类）：\n"
    if persons:
        lines += f"人名（{len(persons)}个）：{'、'.join(persons)}\n"
    if places:
        lines += f"地名（{len(places)}个）：{'、'.join(places)}\n"
    return lines


def build_user_prompt(
    profile: Dict[str, Any],
    text: str,
    include_context: bool = True,
    bilingual: bool = False,
    jieba_hints: list = None,
    ner_hints: dict = None,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> str:
    source_language, target_language = validate_language_pair(source_language, target_language)
    legacy_pair = (
        source_language == DEFAULT_SOURCE_LANGUAGE
        and target_language == DEFAULT_TARGET_LANGUAGE
    )
    pair_key = f"{source_language}->{target_language}"
    pair_examples = (profile.get("fewshot_examples_by_language_pair") or {}).get(pair_key, [])
    if pair_examples:
        fewshot = pair_examples
    elif bilingual and legacy_pair:
        fewshot = profile.get("fewshot_examples_bilingual") or profile.get("fewshot_examples", [])
    elif legacy_pair:
        fewshot = profile.get("fewshot_examples", [])
    else:
        fewshot = []
    if fewshot:
        def _fmt_output(out):
            if isinstance(out, list):
                return json.dumps({"terms": out}, ensure_ascii=False)
            return out
        fewshot_rule = "\n\n【Few-Shot 示例】\n" + "\n".join(
            f"输入：{ex['input']}\n输出：{_fmt_output(ex['output'])}" for ex in fewshot
        )
    else:
        fewshot_rule = ""
    categories_str = "、".join(profile.get("term_categories", []))

    if bilingual and legacy_pair:
        term_field_1 = '"zh_term": "中文术语", "eng_term": "必须原文照抄EN字段中对应的英文"'
        term_field_2 = '"zh_term": "另一个术语", "eng_term": "Copy exact English from EN field"'
    elif bilingual:
        term_field_1 = '"term": "源语言术语", "target_term": "必须从同行目标字段原样照抄的对应术语"'
        term_field_2 = '"term": "另一个源语言术语", "target_term": "同行目标字段中的对应术语"'
    else:
        term_field_1 = '"term": "术语名称"'
        term_field_2 = '"term": "另一个术语"'

    if include_context:
        context_field = ', "context": "原文（≤20字）"'
    else:
        context_field = ""

    common_fields = f'"category": "分类"{context_field}'

    next_step = 5
    if bilingual and legacy_pair:
        bilingual_rule = (
            f"\n{next_step}. 每条文本格式为 \"ZH: 中文 | EN: 英文\"，"
            "zh_term 取 ZH 部分识别出的术语，eng_term 必须原文照抄该术语在 EN 部分对应的英文单词/短语，"
            "严禁使用 EN 字段以外的任何英文（包括你自己的知识库、其他行的内容）"
        )
        next_step += 1
    elif bilingual:
        src_tag = language_tag(source_language)
        tgt_tag = language_tag(target_language)
        bilingual_rule = (
            f"\n{next_step}. 每条文本格式为 \"{src_tag}: {language_name(source_language)} | "
            f"{tgt_tag}: {language_name(target_language)}\"，term 只取 {src_tag} 部分识别出的术语，"
            f"target_term 必须原样照抄同一条目 {tgt_tag} 部分中的对应单词或短语，"
            f"严禁使用 {tgt_tag} 字段以外的内容生成 target_term"
        )
        next_step += 1
    else:
        bilingual_rule = ""

    if include_context:
        unit = "汉字" if legacy_pair else "字符"
        context_rule = f"\n{next_step}. context 填写术语所在的原始文本，最多 20 个{unit}，超出截断"
    else:
        context_rule = ""

    if bilingual and legacy_pair:
        task_desc = (
            "任务：从以下游戏文本条目中提取需要本地化的专业术语。\n\n"
            "文本条目格式为\"[序号] ZH: 中文内容 | EN: 英文内容\"，"
            "请从 ZH 部分识别术语，eng_term 必须原文照抄该条目 EN 部分中对应的英文，"
            "绝对禁止使用 EN 字段以外的英文（包括你的知识库、其他条目、历史翻译）。\n\n"
        )
    elif bilingual:
        src_tag = language_tag(source_language)
        tgt_tag = language_tag(target_language)
        task_desc = (
            f"任务：从以下{language_name(source_language)}游戏文本条目中提取需要本地化的专业术语。\n\n"
            f"文本条目格式为\"[序号] {src_tag}: 源文本 | {tgt_tag}: 同行目标文本\"，"
            f"请只从 {src_tag} 部分识别术语，target_term 必须原样照抄该条目 {tgt_tag} 部分中的对应内容，"
            f"绝对禁止使用 {tgt_tag} 字段以外的内容或其他条目的译文。\n\n"
        )
    else:
        task_desc = (
            f"任务：从以下{language_name(source_language)}游戏文本条目中提取需要本地化的专业术语。\n\n"
            "文本条目格式为\"[序号] 文本内容\"，请从\"文本内容\"部分提取术语。\n\n"
        )

    return (
        "你的回复必须只包含一个 JSON 对象，不得有任何额外文字。\n\n"
        + task_desc
        + "术语分类（必须从以下分类中选择一个）：\n"
        + f"{categories_str}\n\n"
        + _build_rules(profile, source_language)
        + bilingual_rule
        + context_rule
        + fewshot_rule + "\n\n"
        + "输出格式（JSON）：\n"
        + "{{\n"
        + '  "terms": [\n'
        + f'    {{{term_field_1}, {common_fields}}},\n'
        + f'    {{{term_field_2}, {common_fields}}}\n'
        + "  ]\n"
        + "}}\n\n"
        + _build_ner_hint(ner_hints)
        + (f"【术语表命中（以下词在术语库中存在，请判断是否在此语境中是术语）】\n{'、'.join(jieba_hints)}\n\n" if jieba_hints else "")
        + "待分析游戏文本条目：\n"
        + f"{text}\n"
    )


def build_translation_prompt(
    profile,
    terms_with_ref: list,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
) -> tuple:
    source_language, target_language = validate_language_pair(source_language, target_language)
    legacy_pair = (
        source_language == DEFAULT_SOURCE_LANGUAGE
        and target_language == DEFAULT_TARGET_LANGUAGE
    )
    if not legacy_pair:
        hints = "\n".join(
            f"- {t['term']}  |  {language_name(source_language)}语境: {t.get('source_text', '')}"
            f"  |  术语库参考: {t['_ref_term']} → {t['_ref_trans']}"
            for t in terms_with_ref
        )
        game_type = profile.get("game_type", "游戏")
        pair_key = f"{source_language}->{target_language}"
        rules = (profile.get("translation_rules_by_language_pair") or {}).get(pair_key, [])
        rules_block = ("翻译风格规则（必须遵守）：\n" + "\n".join(f"- {r}" for r in rules) + "\n\n") if rules else ""
        system = (
            f"你是一位专业的{game_type}本地化翻译专家，负责将"
            f"{language_name(source_language)}术语翻译成{language_name(target_language)}。"
        )
        user = f"""你是游戏本地化翻译专家。将以下{language_name(source_language)}游戏术语翻译成{language_name(target_language)}。

术语库参考译文仅供参考，请以原文语境为准做出最佳翻译。

{rules_block}严格按 JSON 格式输出，不要任何额外文字：
{{"translations": [{{"term": "源语言术语", "translation": "目标语言译文"}}, ...]}}

{hints}"""
        return system, user

    hints = "\n".join(
        f"- {t['term']}  |  中文语境: {t.get('source_text', '')}  |  术语库参考: {t['_ref_term']} → {t['_ref_trans']}"
        for t in terms_with_ref
    )
    game_type = profile.get("game_type", "游戏")
    rules = profile.get("translation_rules", [])
    rules_block = ("翻译风格规则（必须遵守）：\n" + "\n".join(f"- {r}" for r in rules) + "\n\n") if rules else ""
    system = f"你是一位专业的{game_type}本地化翻译专家。"
    user = f"""你是游戏本地化翻译专家。将以下中文游戏术语翻译成英文。

术语库参考译文仅供参考，请以原文语境为准做出最佳翻译。

{rules_block}严格按 JSON 格式输出，不要任何额外文字：
{{"translations": [{{"term": "中文术语", "translation": "英文译文"}}, ...]}}

{hints}"""
    return system, user
