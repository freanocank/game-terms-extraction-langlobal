#!/usr/bin/env python3
"""Offline CLI entrypoint regression and option-mapping checks."""

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import cli
import core.main as core_main
import ui.ui_backend as backend


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


calls = []
saved = []


def fake_run_pipeline(source_path, glossary_path, profile, api_key, base_url, model, **kwargs):
    glossary_df = pd.read_excel(glossary_path)
    calls.append({
        "source_path": source_path,
        "glossary_path": glossary_path,
        "glossary_columns": list(glossary_df.columns),
        "glossary_empty": glossary_df.empty,
        "profile": profile,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        **kwargs,
    })
    return [{
        "term": "墨门",
        "category": "门派势力",
        "translation": "Momen",
        "match_type": "exact",
    }]


def fake_save_outputs(terms, out_dir, **kwargs):
    saved.append({"terms": terms, "out_dir": Path(out_dir), **kwargs})
    return Path(out_dir) / "results.xlsx", Path(out_dir) / "候选术语_模板.xlsx"


cli.run_pipeline = fake_run_pipeline
cli.save_outputs = fake_save_outputs


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    source_path = tmp_path / "source.xlsx"
    output_path = tmp_path / "result"
    checkpoint_path = tmp_path / "checkpoint"
    pd.DataFrame({
        "文本 ID": ["A1"],
        "中文原文": ["墨门弟子抵达千年渡。"],
        "英文译文": ["A Momen disciple arrived at Millennial Ferry."],
    }).to_excel(source_path, index=False)

    returned = cli.main([
        "--source", str(source_path),
        "--profile", "yanyun",
        "--api-key", "test-key",
        "--base-url", "https://example.invalid/v1",
        "--model", "test-model",
        "--ner-api-key", "ner-test-key",
        "--ner-base-url", "https://ner.example.invalid/v1",
        "--ner-model", "ner-test-model",
        "--embedding-api-key", "embedding-test-key",
        "--embedding-base-url", "https://embedding.example.invalid/v1",
        "--embedding-model", "embedding-test-model",
        "--output", str(output_path),
        "--checkpoint", str(checkpoint_path),
        "--src-col", "1",
        "--key-col", "0",
        "--bilingual",
        "--src-en-col", "2",
        "--no-translate",
        "--no-flag-unsourced",
        "--concurrent", "7",
        "--max-tokens", "8192",
        "--embed-workers", "5",
    ])

    ck("source-only CLI reaches the shared pipeline", len(calls) == 1, calls)
    call = calls[0]
    ck("source-only CLI supplies a valid empty glossary",
       call["glossary_empty"] and call["glossary_columns"] == ["中文术语", "英文译文"], call)
    ck("CLI forwards service and project settings", (
        call["profile"] == "yanyun"
        and call["api_key"] == "test-key"
        and call["base_url"] == "https://example.invalid/v1"
        and call["model"] == "test-model"
    ), call)
    opts = call["opts"]
    ck("CLI forwards source and glossary column mappings", (
        opts.src_col == 1 and opts.key_col == 0 and opts.src_en_col == 2
        and opts.gl_cn_col == 0 and opts.gl_en_col == 1
    ), opts)
    ck("CLI forwards bilingual and extraction-only modes", opts.bilingual and opts.no_translate, opts)
    ck("CLI forwards review and performance options", (
        not opts.flag_unsourced and opts.max_concurrent == 7
        and opts.max_tokens == 8192 and opts.embed_workers == 5
    ), opts)
    ck("CLI forwards independently configured NER and Embedding services", (
        not opts.ner_use_main
        and opts.ner_api_key == "ner-test-key"
        and opts.ner_base_url == "https://ner.example.invalid/v1"
        and opts.ner_model == "ner-test-model"
        and not opts.embedding_use_main
        and opts.embedding_api_key == "embedding-test-key"
        and opts.embedding_base_url == "https://embedding.example.invalid/v1"
        and opts.embedding_model == "embedding-test-model"
    ), opts)
    ck("CLI uses one run directory for logs and raw data", (
        Path(call["output_dir"]) == output_path
        and Path(call["raw_dir"]) == output_path / "raw_data"
        and Path(call["checkpoint_dir"]) == checkpoint_path
    ), call)
    ck("CLI exports the same pipeline results", (
        returned == saved[0]["terms"]
        and saved[0]["out_dir"] == output_path
        and saved[0]["profile_name"] == "yanyun"
        and saved[0]["source_path"] == str(source_path)
    ), saved)


defaults = cli.build_parser().parse_args(["--source", "source.xlsx"])
ck("glossary is optional in the CLI contract", defaults.glossary == "", defaults.glossary)


ui_calls = []
ui_saves = []


def fake_ui_run(source_path, glossary_path, profile, api_key, base_url, model, **kwargs):
    checkpoint_dir = Path(kwargs["checkpoint_dir"])
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "pipeline.marker").write_text("complete", encoding="utf-8")
    ui_calls.append({
        "source_path": source_path,
        "glossary_path": glossary_path,
        "profile": profile,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        **kwargs,
    })
    return [{"term": "墨门", "translation": "Momen", "match_type": "exact"}]


def fake_ui_save(terms, out_dir, **kwargs):
    checkpoint_dir = Path(ui_calls[-1]["checkpoint_dir"])
    ui_saves.append({
        "checkpoint_exists": (checkpoint_dir / "pipeline.marker").exists(),
        "terms": terms,
        "out_dir": Path(out_dir),
        **kwargs,
    })
    return Path(out_dir) / "results.xlsx", Path(out_dir) / "候选术语_模板.xlsx"


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    backend.ROOT = tmp_path / "ui_root"
    source_path = tmp_path / "source.xlsx"
    glossary_path = tmp_path / "glossary.xlsx"
    pd.DataFrame({"文本 ID": ["A1"], "中文原文": ["墨门弟子"], "英文译文": ["Momen disciple"]}).to_excel(
        source_path, index=False
    )
    pd.DataFrame({"中文术语": ["墨门"], "英文译文": ["Momen"]}).to_excel(glossary_path, index=False)
    core_main.run_pipeline = fake_ui_run
    core_main.save_outputs = fake_ui_save

    cfg = backend.RunConfig(
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model="test-model",
        ner_use_main=False,
        ner_api_key="ner-test-key",
        ner_api_base="https://ner.example.invalid/v1",
        ner_model="ner-test-model",
        embedding_use_main=False,
        embedding_api_key="embedding-test-key",
        embedding_api_base="https://embedding.example.invalid/v1",
        embedding_model="embedding-test-model",
        profile="yanyun",
        max_concurrent=7,
        embed_workers=5,
        max_tokens=8192,
        bilingual=True,
        no_translate=True,
        flag_unsourced=False,
    )
    task = backend.ProcessingTask()
    selected_checkpoint = tmp_path / "selected_checkpoint"
    task.start(
        str(source_path), str(glossary_path), cfg, None,
        src_col=1, gl_cn_col=0, gl_en_col=1, src_en_col=2,
        src_bytes=source_path.read_bytes(), key_col=0,
        checkpoint_dir=str(selected_checkpoint),
    )
    task._thread.join(timeout=10)

    ui_call = ui_calls[0]
    ui_opts = ui_call["opts"]
    cli_opts = calls[0]["opts"]
    ck("UI adapter and CLI map the same pipeline options", ui_opts == cli_opts,
       {"ui": ui_opts, "cli": cli_opts})
    ck("UI and CLI keep logs and raw data inside the run directory", (
        Path(ui_call["raw_dir"]) == Path(ui_call["output_dir"]) / "raw_data"
        and Path(calls[0]["raw_dir"]) == Path(calls[0]["output_dir"]) / "raw_data"
    ), {"ui": ui_call, "cli": calls[0]})
    ck("UI saves results before clearing a completed checkpoint", (
        ui_saves[0]["checkpoint_exists"]
        and not Path(ui_call["checkpoint_dir"]).exists()
        and task.results == ui_saves[0]["terms"]
    ), {"save": ui_saves, "task_error": task.error})
    ck("UI resumes the exact selected checkpoint directory",
       Path(ui_call["checkpoint_dir"]) == selected_checkpoint, ui_call["checkpoint_dir"])
    ck("UI task snapshots the selected run configuration", task.run_config == cfg, task.run_config)


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
