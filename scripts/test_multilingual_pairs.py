#!/usr/bin/env python3
"""Offline regression checks for configurable source/target language pairs."""

import hashlib
import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import cli
import core.main as core_main
from core.checkpoint import checkpoint_dir_name
from core.header_detect import detect_glossary_columns, detect_source_column
from core.languages import normalize_language_code, validate_language_pair
from core.main import (
    PipelineOpts,
    _aligned_target_value,
    _collect_llm_votes,
    _count_llm_votes,
    _batch_match_context,
    empty_glossary_bytes,
    match_and_translate,
    prepare_source_texts,
)
from core.prompt_base import build_system_prompt, build_translation_prompt, build_user_prompt


results = []


def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    suffix = f"  >> {detail}" if detail and not ok else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")


check("language aliases normalize to canonical codes", (
    normalize_language_code("EN_us") == "en"
    and normalize_language_code("pt_br") == "pt-BR"
    and normalize_language_code("sr-Latn-rs") == "sr-Latn-RS"
))

try:
    validate_language_pair("de", "DE-de")
except ValueError:
    same_language_rejected = True
else:
    same_language_rejected = False
check("same normalized source and target are rejected", same_language_rejected)


PROFILE = {
    "game_type": "奇幻 RPG",
    "term_categories": ["角色名", "地点", "技能"],
    "core_principle": "中文人名必须提取",
    "core_principle_by_source_language": {"en": "提取英文项目专名"},
    "extract_examples": {"include": ["中文人名示例"]},
    "extract_examples_by_source_language": {"en": {"include": ["English multi-word proper names"]}},
    "extraction_notes": ["中文人名模式强制提取"],
    "extraction_notes_by_source_language": {"en": ["保留英文多词专名"]},
    "fewshot_examples": [{"input": "中文示例", "output": [{"term": "示例", "category": "技能"}]}],
    "translation_rules": ["中文角色名使用拼音"],
    "translation_rules_by_language_pair": {
        "en->fr": ["保留正式奇幻语域"],
    },
}

paired_prompt = build_user_prompt(
    PROFILE,
    "[1] EN: Training Afternoon | DE: Trainingsnachmittag",
    bilingual=True,
    source_language="en",
    target_language="de",
)
generic_system_prompt = build_system_prompt(PROFILE, source_language="en")
check("aligned multilingual extraction uses generic fields and language tags", (
    '"term"' in paired_prompt
    and '"target_term"' in paired_prompt
    and "EN:" in paired_prompt
    and "DE:" in paired_prompt
    and '"zh_term"' not in paired_prompt
))
check("unscoped Chinese few-shot is not injected into another language pair", "中文示例" not in paired_prompt)
check("non-Chinese source uses only its language-scoped profile rules", (
    "提取英文项目专名" in generic_system_prompt
    and "English multi-word proper names" in generic_system_prompt
    and "中文人名必须提取" not in generic_system_prompt
    and "中文人名示例" not in generic_system_prompt
    and "保留英文多词专名" in paired_prompt
    and "中文人名模式强制提取" not in paired_prompt
))

translation_system, translation_user = build_translation_prompt(
    PROFILE,
    [{"term": "Moonkeep", "source_text": "Enter Moonkeep", "_ref_term": "", "_ref_trans": ""}],
    source_language="en",
    target_language="fr",
)
check("translation prompt names the selected source and target languages", (
    "英语" in translation_system
    and "法语" in translation_system
    and "保留正式奇幻语域" in translation_user
))
check("pair-specific translation rules do not reuse Chinese-English rules", "中文角色名使用拼音" not in translation_user)


source_df = pd.DataFrame({
    "ID": [101, 102, 103],
    "Note": ["", "", ""],
    "EN-v2": ["Training Afternoon", "...", "Training Afternoon"],
    "DE-MT": ["Trainingsnachmittag", "...", "Trainingsnachmittag"],
    "FR-MT": ["Après-midi d’entraînement", "...", "Après-midi d’entraînement"],
    "CN": ["训练下午", "……", "训练下午"],
})

de_mapping = detect_source_column(source_df, source_language="en", target_language="de")
fr_mapping = detect_source_column(source_df, source_language="en", target_language="fr")
check("EN→DE headers are detected without an API call", de_mapping == {
    "text_col": 2, "key_col": 0, "en_col": 3,
    "method": "rule", "confidence": "高（语言列名匹配，请确认）",
}, de_mapping)
check("EN→FR headers select the French column", fr_mapping.get("en_col") == 4, fr_mapping)

glossary_mapping = detect_glossary_columns(
    pd.DataFrame({"EN": ["Moonkeep"], "FR": ["Fort-de-Lune"]}),
    source_language="en",
    target_language="fr",
)
check("multilingual glossary headers are detected", (
    glossary_mapping.get("cn_col") == 0
    and glossary_mapping.get("en_col") == 1
    and glossary_mapping.get("method") == "rule"
), glossary_mapping)

other_language_mapping = detect_source_column(
    pd.DataFrame({"ID": [1], "sr-Latn": ["Mesečeva tvrđava"], "DE": ["Mondfeste"]}),
    source_language="sr-Latn",
    target_language="de",
)
check("code-named headers also work for languages outside the UI registry", (
    other_language_mapping.get("text_col") == 1
    and other_language_mapping.get("en_col") == 2
    and other_language_mapping.get("key_col") == 0
    and other_language_mapping.get("method") == "rule"
), other_language_mapping)

texts, keys = prepare_source_texts(source_df, PipelineOpts(
    source_language="en",
    target_language="de",
    bilingual=True,
    src_col=2,
    src_target_col=3,
    key_col=0,
))
check("aligned source preparation preserves pair labels, filtering, deduplication, and keys", (
    texts == ["EN: Training Afternoon | DE: Trainingsnachmittag"]
    and keys == ["101"]
), {"texts": texts, "keys": keys})

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    source_path = tmp_path / "source.xlsx"
    source_df.to_excel(source_path, index=False)
    real_extract_terms = core_main.extract_terms

    def fake_extract_terms(source_texts, *args, **kwargs):
        target_language = kwargs["opts"].target_language
        target_term = {
            "de": "Trainingsnachmittag",
            "fr": "Après-midi d’entraînement",
        }[target_language]
        return [{
            "term": "Training Afternoon",
            "target_term": target_term,
            "category": "UI系统",
            "source_text": source_texts[0],
            "source_key": kwargs["text_keys"][0],
        }]

    core_main.extract_terms = fake_extract_terms
    try:
        pipeline_results = {}
        for target_language, target_col in (("de", 3), ("fr", 4)):
            glossary_path = tmp_path / f"empty_{target_language}.xlsx"
            glossary_path.write_bytes(empty_glossary_bytes("en", target_language))
            pipeline_results[target_language] = core_main.run_pipeline(
                str(source_path),
                str(glossary_path),
                "yanyun",
                output_dir=str(tmp_path / f"output_{target_language}"),
                opts=PipelineOpts(
                    source_language="en",
                    target_language=target_language,
                    bilingual=True,
                    no_translate=True,
                    src_col=2,
                    src_target_col=target_col,
                    key_col=0,
                ),
            )
        exact_glossary_path = tmp_path / "exact_de.xlsx"
        pd.DataFrame({
            "English term": ["Training Afternoon"],
            "German translation": ["Freigegebener Glossarwert"],
        }).to_excel(exact_glossary_path, index=False)
        pipeline_results["de_existing_glossary"] = core_main.run_pipeline(
            str(source_path),
            str(exact_glossary_path),
            "yanyun",
            output_dir=str(tmp_path / "output_de_existing_glossary"),
            opts=PipelineOpts(
                source_language="en",
                target_language="de",
                bilingual=True,
                no_translate=True,
                src_col=2,
                src_target_col=3,
                key_col=0,
            ),
        )
    finally:
        core_main.extract_terms = real_extract_terms

check("shared pipeline runs EN→DE and EN→FR with isolated language metadata", (
    pipeline_results["de"][0]["translation"] == "Trainingsnachmittag"
    and pipeline_results["de"][0]["source_language"] == "en"
    and pipeline_results["de"][0]["target_language"] == "de"
    and pipeline_results["fr"][0]["translation"] == "Après-midi d’entraînement"
    and pipeline_results["fr"][0]["target_language"] == "fr"
    and pipeline_results["de"][0]["source_key"] == "101"
    and pipeline_results["fr"][0]["source_key"] == "101"
), pipeline_results)
check("existing-only pipeline still prefers an exact glossary translation", (
    pipeline_results["de_existing_glossary"][0]["translation"] == "Freigegebener Glossarwert"
    and pipeline_results["de_existing_glossary"][0]["match_type"] == "exact"
), pipeline_results["de_existing_glossary"])


raw_votes = [[{
    "extracted_terms": {"terms": [{
        "term": "Training Afternoon",
        "target_term": "Trainingsnachmittag",
        "category": "技能",
    }]},
}] for _ in range(3)]
votes = _collect_llm_votes(raw_votes)
voted_terms = _count_llm_votes(votes, PROFILE["term_categories"])
check("three-vote extraction retains the aligned target term", voted_terms == [{
    "term": "Training Afternoon",
    "category": "技能",
    "target_term": "Trainingsnachmittag",
}], voted_terms)

copied = match_and_translate(
    [{
        "term": "Training Afternoon",
        "target_term": "Trainingsnachmittag",
        "source_text": "EN: Training Afternoon | DE: Trainingsnachmittag",
        "category": "技能",
    }],
    {},
    PROFILE,
    "",
    "",
    "unused",
    bilingual=True,
    source_language="en",
    target_language="de",
)
check("verified aligned target text is copied without translation", (
    copied[0]["translation"] == "Trainingsnachmittag"
    and copied[0]["match_type"] == "aligned"
    and "target_term" not in copied[0]
), copied)

case_variant = {
    "target_term": "trainingsnachmittag",
    "source_text": "EN: Training Afternoon | DE: Trainingsnachmittag",
}
check("aligned copy preserves the target cell's original casing", (
    _aligned_target_value(case_variant, True, "de") == "Trainingsnachmittag"
))

empty_target_echo = {
    "term": "青长老",
    "target_term": "青长老",
    "source_text": "ZH: 青长老说话 | EN:",
}
check("empty aligned target rejects a model echo of the source term", (
    _aligned_target_value(empty_target_echo, True, "en") == ""
    and "译文为空" in empty_target_echo.get("note", "")
), empty_target_echo)


class EchoGuardTranslator:
    def __init__(self, **kwargs):
        pass

    def translate_batches(self, prompts):
        return {"青长老": "Elder Qing"}


real_translator = core_main.TermTranslator
core_main.TermTranslator = EchoGuardTranslator
try:
    echo_fallback = match_and_translate(
        [dict(empty_target_echo)], {}, PROFILE, "", "", "unused",
        bilingual=True, source_language="zh-Hans", target_language="en",
    )
finally:
    core_main.TermTranslator = real_translator
check("empty aligned target falls back to translation instead of echoing source", (
    echo_fallback[0]["translation"] == "Elder Qing"
    and echo_fallback[0]["match_type"] == "llm_translated"
), echo_fallback)

echo_existing_only = match_and_translate(
    [dict(empty_target_echo)], {}, PROFILE, "", "", "unused",
    bilingual=True, source_language="zh-Hans", target_language="en",
    translate_missing=False,
)
check("empty aligned target stays blank in existing-only mode", (
    echo_existing_only[0]["translation"] == ""
    and echo_existing_only[0]["match_type"] == "no_translate"
), echo_existing_only)

empty_target_whitespace = {
    "target_term": "青长老",
    "source_text": "ZH: 青长老说话| EN:   ",
}
check("whitespace-only aligned target is treated as empty", (
    _aligned_target_value(empty_target_whitespace, True, "en") == ""
), empty_target_whitespace)

invalid_aligned = {
    "target_term": "Invented German",
    "source_text": "EN: Training Afternoon | DE: Trainingsnachmittag",
}
check("aligned output not present in the target segment is rejected", (
    _aligned_target_value(invalid_aligned, True, "de") == ""
    and "未命中" in invalid_aligned.get("note", "")
), invalid_aligned)

unsourced_aligned = {"target_term": "Invented German"}
check("aligned output without a source snapshot is rejected", (
    _aligned_target_value(unsourced_aligned, True, "de") == ""
    and "无法核对" in unsourced_aligned.get("note", "")
), unsourced_aligned)

source_only_match = [{"term": "Trainingsnachmittag"}]
_batch_match_context(
    source_only_match,
    ["EN: Training Afternoon | DE: Trainingsnachmittag"],
    ["101"],
    "en",
    "de",
)
check("a term found only in the target segment is not attributed to the source", (
    source_only_match[0]["source_text"] == ""
    and source_only_match[0]["source_key"] == ""
), source_only_match)

default_pair_target_only = [{"term": "EnglishOnlyTerm"}]
_batch_match_context(
    default_pair_target_only,
    ["ZH-HANS: 中文句子 | EN: EnglishOnlyTerm"],
    ["CN001"],
    "zh-Hans",
    "en",
)
check("default ZH-HANS→EN matching also ignores the target segment", (
    default_pair_target_only[0]["source_text"] == ""
    and default_pair_target_only[0]["source_key"] == ""
), default_pair_target_only)


def mode_terms(bilingual):
    terms = [
        {"term": "Glossary Hit", "category": "UI系统"},
        {"term": "Aligned Only", "category": "UI系统"},
        {"term": "Missing Term", "category": "UI系统"},
    ]
    if bilingual:
        targets = ["Workbook Override", "Ausgerichtet", ""]
        for term, target in zip(terms, targets):
            term["target_term"] = target
            term["source_text"] = f"EN: {term['term']} | DE: {target}"
    return terms


MODE_GLOSSARY = {"glossary hit": ("Glossary Hit", "Glossarwert")}


class FakeTermTranslator:
    def __init__(self, **kwargs):
        pass

    def translate_batches(self, prompts):
        return {
            "Aligned Only": "KI ausgerichtet",
            "Missing Term": "KI fehlend",
        }


real_translator = core_main.TermTranslator
core_main.TermTranslator = FakeTermTranslator
try:
    mono_auto = match_and_translate(
        mode_terms(False), MODE_GLOSSARY, PROFILE, "", "", "unused",
        source_language="en", target_language="de",
    )
    bilingual_auto = match_and_translate(
        mode_terms(True), MODE_GLOSSARY, PROFILE, "", "", "unused",
        bilingual=True, source_language="en", target_language="de",
    )
finally:
    core_main.TermTranslator = real_translator

mono_existing_only = match_and_translate(
    mode_terms(False), MODE_GLOSSARY, PROFILE, "", "", "unused",
    source_language="en", target_language="de", translate_missing=False,
)
bilingual_existing_only = match_and_translate(
    mode_terms(True), MODE_GLOSSARY, PROFILE, "", "", "unused",
    bilingual=True, source_language="en", target_language="de", translate_missing=False,
)

mode_results = {
    "mono_auto": {item["term"]: item for item in mono_auto},
    "bilingual_auto": {item["term"]: item for item in bilingual_auto},
    "mono_existing_only": {item["term"]: item for item in mono_existing_only},
    "bilingual_existing_only": {item["term"]: item for item in bilingual_existing_only},
}
check("single-language + auto uses glossary then AI", (
    mode_results["mono_auto"]["Glossary Hit"]["match_type"] == "exact"
    and mode_results["mono_auto"]["Glossary Hit"]["translation"] == "Glossarwert"
    and mode_results["mono_auto"]["Aligned Only"]["match_type"] == "llm_translated"
    and mode_results["mono_auto"]["Missing Term"]["translation"] == "KI fehlend"
), mode_results["mono_auto"])
check("aligned bilingual + auto uses glossary, aligned row, then AI", (
    mode_results["bilingual_auto"]["Glossary Hit"]["match_type"] == "exact"
    and mode_results["bilingual_auto"]["Aligned Only"]["match_type"] == "aligned"
    and mode_results["bilingual_auto"]["Aligned Only"]["translation"] == "Ausgerichtet"
    and mode_results["bilingual_auto"]["Missing Term"]["match_type"] == "llm_translated"
), mode_results["bilingual_auto"])
check("single-language + existing-only keeps glossary hits and leaves the rest blank", (
    mode_results["mono_existing_only"]["Glossary Hit"]["match_type"] == "exact"
    and mode_results["mono_existing_only"]["Aligned Only"]["match_type"] == "no_translate"
    and mode_results["mono_existing_only"]["Aligned Only"]["translation"] == ""
    and mode_results["mono_existing_only"]["Missing Term"]["translation"] == ""
), mode_results["mono_existing_only"])
check("aligned bilingual + existing-only keeps glossary and aligned rows, leaving gaps blank", (
    mode_results["bilingual_existing_only"]["Glossary Hit"]["match_type"] == "exact"
    and mode_results["bilingual_existing_only"]["Aligned Only"]["match_type"] == "aligned"
    and mode_results["bilingual_existing_only"]["Aligned Only"]["translation"] == "Ausgerichtet"
    and mode_results["bilingual_existing_only"]["Missing Term"]["match_type"] == "no_translate"
    and mode_results["bilingual_existing_only"]["Missing Term"]["translation"] == ""
), mode_results["bilingual_existing_only"])


empty_df = pd.read_excel(BytesIO(empty_glossary_bytes("en", "fr")))
check("empty reference glossary follows the selected language pair", list(empty_df.columns) == ["英语术语", "法语译文"])

payload = b"same source workbook"
digest = hashlib.md5(payload).hexdigest()[:8]
default_checkpoint = checkpoint_dir_name(payload, "yanyun")
de_checkpoint = checkpoint_dir_name(payload, "yanyun", "en", "de")
fr_checkpoint = checkpoint_dir_name(payload, "yanyun", "en", "fr")
check("legacy checkpoint naming remains unchanged", default_checkpoint == f"src_yanyun_{digest}", default_checkpoint)
check("checkpoint namespaces are isolated by language pair", len({default_checkpoint, de_checkpoint, fr_checkpoint}) == 3)

parsed = cli.build_parser().parse_args([
    "--source", "sample.xlsx",
    "--source-lang", "sr-Latn",
    "--target-lang", "de",
    "--src-target-col", "4",
    "--gl-source-col", "1",
    "--gl-target-col", "2",
])
check("CLI accepts arbitrary valid language codes and generic column options", (
    parsed.source_language == "sr-Latn"
    and parsed.target_language == "de"
    and parsed.src_target_col == 4
    and parsed.gl_source_col == 1
    and parsed.gl_target_col == 2
))


passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
