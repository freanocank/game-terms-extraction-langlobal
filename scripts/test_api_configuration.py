#!/usr/bin/env python3
"""Offline regression checks for configurable API services and secret handling."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import core.header_detect as header_detect
import core.main as core_main
import ui.ui_backend as backend
from core.embed_store import EmbedStore


results = []


def ck(name, condition, detail=""):
    ok = bool(condition)
    results.append((name, ok))
    suffix = f"  >> {str(detail)[:180]}" if detail and not ok else ""
    print(("PASS" if ok else "FAIL") + " | " + name + suffix)


MAIN_SECRET = "secret-main-sentinel"
NER_SECRET = "secret-ner-sentinel"
EMBED_SECRET = "secret-embedding-sentinel"
DETECT_SECRET = "secret-detect-sentinel"
ALL_SECRETS = (MAIN_SECRET, NER_SECRET, EMBED_SECRET, DETECT_SECRET)


original_config_file = backend.CONFIG_FILE
with tempfile.TemporaryDirectory() as tmp:
    temp_path = Path(tmp)
    backend.CONFIG_FILE = temp_path / ".ui_config.json"
    backend.CONFIG_FILE.write_text(json.dumps({
        "api_key": "legacy-main-secret",
        "ner_api_key": "legacy-ner-secret",
        "embedding_api_key": "legacy-embedding-secret",
        "detect_api_key": "legacy-detect-secret",
        "api_base": "https://saved-main.example/v1",
        "model": "saved-main-model",
        "profile": "yanyun",
        "ner_use_main": False,
        "ner_api_base": "https://saved-ner.example/v1",
        "ner_model": "saved-ner-model",
    }), encoding="utf-8")
    cfg = backend.RunConfig(
        api_key=MAIN_SECRET,
        ner_api_key=NER_SECRET,
        embedding_api_key=EMBED_SECRET,
        detect_api_key=DETECT_SECRET,
    )
    backend._load_persisted_config(cfg)
    migrated = json.loads(backend.CONFIG_FILE.read_text(encoding="utf-8"))
    migrated_text = backend.CONFIG_FILE.read_text(encoding="utf-8")
    ck("legacy persisted API keys are removed during migration", not any(
        "key" in name.lower() or "token" in name.lower() or "secret" in name.lower()
        for name in migrated
    ), migrated)
    ck("legacy persisted secrets never replace runtime secrets", (
        cfg.api_key == MAIN_SECRET
        and cfg.ner_api_key == NER_SECRET
        and cfg.embedding_api_key == EMBED_SECRET
        and cfg.detect_api_key == DETECT_SECRET
    ), cfg)
    ck("non-secret persisted settings still load", (
        cfg.api_base == "https://saved-main.example/v1"
        and cfg.model == "saved-main-model"
        and not cfg.ner_use_main
        and cfg.ner_model == "saved-ner-model"
    ), cfg)
    ck("migration output contains no secret sentinel", not any(s in migrated_text for s in ALL_SECRETS))

    backend.save_persisted_config(cfg)
    saved_text = backend.CONFIG_FILE.read_text(encoding="utf-8")
    ck("normal config save never writes API keys", not any(s in saved_text for s in ALL_SECRETS), saved_text)
    if os.name != "nt":
        ck("persisted non-secret config uses owner-only permissions",
           backend.CONFIG_FILE.stat().st_mode & 0o777 == 0o600,
           oct(backend.CONFIG_FILE.stat().st_mode & 0o777))

backend.CONFIG_FILE = original_config_file


cfg = backend.RunConfig(
    api_key=MAIN_SECRET,
    api_base="https://main.example/v1",
    model="main-model",
    ner_use_main=True,
    ner_api_key=NER_SECRET,
    ner_api_base="https://ner.example/v1",
    ner_model="ner-model",
    embedding_use_main=False,
    embedding_api_key=EMBED_SECRET,
    embedding_api_base="https://embedding.example/v1",
    embedding_model="embedding-model",
    detect_use_main=False,
    detect_api_key=DETECT_SECRET,
    detect_api_base="https://detect.example/v1",
    detect_model="detect-model",
)
ner = backend.resolve_service_settings(cfg, "ner")
embedding = backend.resolve_service_settings(cfg, "embedding")
detect = backend.resolve_service_settings(cfg, "detect")
ck("NER can inherit the main key/address while keeping its own model", (
    ner.api_key == MAIN_SECRET
    and ner.api_base == "https://main.example/v1"
    and ner.model == "ner-model"
), ner)
cfg.ner_use_main = False
cfg.ner_api_key = ""
ner_with_blank_key = backend.resolve_service_settings(cfg, "ner")
ck("an empty auxiliary key still falls back to the main runtime key", (
    ner_with_blank_key.api_key == MAIN_SECRET
    and ner_with_blank_key.api_base == "https://ner.example/v1"
), ner_with_blank_key)
ck("Embedding can use an independent key/address/model", (
    embedding.api_key == EMBED_SECRET
    and embedding.api_base == "https://embedding.example/v1"
    and embedding.model == "embedding-model"
), embedding)
ck("header detection can use an independent key/address/model", (
    detect.api_key == DETECT_SECRET
    and detect.api_base == "https://detect.example/v1"
    and detect.model == "detect-model"
), detect)
ck("RunConfig repr redacts all API keys", not any(s in repr(cfg) for s in ALL_SECRETS), repr(cfg))


connection_calls = []


class FakeConnectionClient:
    def __init__(self, api_key, base_url, timeout):
        connection_calls.append(("client", api_key, base_url))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.embeddings = SimpleNamespace(create=self._embedding)

    @staticmethod
    def _chat(**kwargs):
        connection_calls.append(("chat", kwargs["model"]))
        return SimpleNamespace(choices=[])

    @staticmethod
    def _embedding(**kwargs):
        connection_calls.append(("embedding", kwargs["model"]))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.0])])


cfg.ner_api_key = NER_SECRET
fake_openai_module = SimpleNamespace(OpenAI=FakeConnectionClient)
with patch.dict(sys.modules, {"openai": fake_openai_module}):
    connection_results = {
        service: backend.test_api_connection(cfg, service)
        for service in ("main", "ner", "embedding", "detect")
    }
ck("connection tests exercise every configured Chat/Embedding endpoint", (
    all(ok for ok, _ in connection_results.values())
    and sum(1 for call in connection_calls if call[0] == "chat") == 3
    and sum(1 for call in connection_calls if call[0] == "embedding") == 1
    and ("client", MAIN_SECRET, "https://main.example/v1") in connection_calls
    and ("client", NER_SECRET, "https://ner.example/v1") in connection_calls
    and ("client", EMBED_SECRET, "https://embedding.example/v1") in connection_calls
    and ("client", DETECT_SECRET, "https://detect.example/v1") in connection_calls
), {"results": connection_results, "calls": connection_calls})


class FailingConnectionClient:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(f"provider rejected {MAIN_SECRET}")


with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FailingConnectionClient)}):
    failed_ok, failed_message = backend.test_api_connection(cfg, "main")
ck("connection-test errors redact API keys", not failed_ok and MAIN_SECRET not in failed_message, failed_message)


with tempfile.TemporaryDirectory() as tmp:
    original_cache = header_detect.CACHE_FILE
    original_openai = header_detect.OpenAI
    detection_calls = []

    class FakeDetectionCompletions:
        def create(self, **kwargs):
            detection_calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"text_col": 1, "key_col": 0, "en_col": null}')
            )])

    class FakeDetectionOpenAI:
        def __init__(self, api_key, base_url, timeout):
            detection_calls.append({"api_key": api_key, "base_url": base_url, "timeout": timeout})
            self.chat = SimpleNamespace(completions=FakeDetectionCompletions())

    try:
        header_detect.CACHE_FILE = Path(tmp) / "header_cache.json"
        header_detect.OpenAI = FakeDetectionOpenAI
        detected = header_detect.detect_source_column(
            pd.DataFrame({"Key": ["A1"], "原文": ["墨门弟子"]}),
            api_key=DETECT_SECRET,
            base_url="https://detect.example/v1",
            model="detect-model",
        )
    finally:
        header_detect.CACHE_FILE = original_cache
        header_detect.OpenAI = original_openai
    ck("header detection receives its explicit runtime credentials", (
        detection_calls[0]["api_key"] == DETECT_SECRET
        and detection_calls[0]["base_url"] == "https://detect.example/v1"
        and detection_calls[1]["model"] == "detect-model"
        and detected["method"] == "ai"
    ), detection_calls)


with tempfile.TemporaryDirectory() as tmp:
    original_ner = core_main._ner_scan
    original_votes = core_main._run_batch_votes
    ner_calls = []

    def fake_ner(batch, api_key, base_url, model):
        ner_calls.append((api_key, base_url, model))
        return [], []

    vote = {"custom_id": "fake", "extracted_terms": {"terms": [
        {"term": "墨门", "category": "门派势力"},
    ]}}
    try:
        core_main._ner_scan = fake_ner
        core_main._run_batch_votes = lambda *args, **kwargs: [[vote], [vote], [vote]]
        opts = core_main.PipelineOpts(
            max_concurrent=1,
            max_tokens=1024,
            ner_api_key=NER_SECRET,
            ner_base_url="https://ner.example/v1",
            ner_model="ner-model",
        )
        core_main.extract_terms(
            ["墨门弟子抵达千年渡"],
            {"term_categories": ["门派势力"], "address_suffixes": []},
            MAIN_SECRET,
            "https://main.example/v1",
            "main-model",
            output_dir=tmp,
            opts=opts,
        )
    finally:
        core_main._ner_scan = original_ner
        core_main._run_batch_votes = original_votes
    ck("NER pre-scan receives its independent runtime service", ner_calls == [(
        NER_SECRET, "https://ner.example/v1", "ner-model"
    )], ner_calls)


with tempfile.TemporaryDirectory() as tmp:
    temp_path = Path(tmp)
    source_path = temp_path / "source.xlsx"
    glossary_path = temp_path / "glossary.xlsx"
    checkpoint_path = temp_path / "checkpoint"
    pd.DataFrame({"原文": ["墨门弟子"]}).to_excel(source_path, index=False)
    pd.DataFrame({"中文术语": ["墨门"], "英文译文": ["Momen"]}).to_excel(glossary_path, index=False)

    original_store = core_main.EmbedStore
    original_extract = core_main.extract_terms
    embed_calls = []

    class FakeEmbedStore:
        def __init__(self, db_path, api_key, base_url, model):
            embed_calls.append((api_key, base_url, model))
            self.count = 0

        def sync(self, terms, **kwargs):
            self.count = len(terms)
            return len(terms), 0

        def search(self, queries):
            return [("", 0.0)] * len(queries)

    try:
        core_main.EmbedStore = FakeEmbedStore
        core_main.extract_terms = lambda *args, **kwargs: []
        opts = core_main.PipelineOpts(
            embedding_api_key=EMBED_SECRET,
            embedding_base_url="https://embedding.example/v1",
            embedding_model="embedding-model",
            ner_api_key=NER_SECRET,
            ner_base_url="https://ner.example/v1",
            ner_model="ner-model",
        )
        core_main.run_pipeline(
            str(source_path),
            str(glossary_path),
            "yanyun",
            MAIN_SECRET,
            "https://main.example/v1",
            "main-model",
            output_dir=str(temp_path / "output"),
            checkpoint_dir=str(checkpoint_path),
            opts=opts,
        )
    finally:
        core_main.EmbedStore = original_store
        core_main.extract_terms = original_extract

    meta_text = (checkpoint_path / "run_meta.json").read_text(encoding="utf-8")
    ck("Embedding receives its independent runtime service", embed_calls == [(
        EMBED_SECRET, "https://embedding.example/v1", "embedding-model"
    )], embed_calls)
    ck("checkpoint metadata contains no API key", not any(s in meta_text for s in ALL_SECRETS), meta_text)


with tempfile.TemporaryDirectory() as tmp:
    db_path = Path(tmp) / "embeddings.db"
    first = EmbedStore(str(db_path), "first-key", "https://provider-a.example/v1", "same-model")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO embeddings (term, emb, dim) VALUES (?, ?, ?)",
            ("墨门", b"\x00\x00\x00\x00", 1),
        )
    ck("embedding cache fixture is populated", first.count == 1, first.count)
    second = EmbedStore(str(db_path), "second-key", "https://provider-b.example/v1", "same-model")
    ck("same model on a different provider invalidates stale embeddings", second.count == 0, second.count)


with tempfile.TemporaryDirectory() as tmp:
    temp_config = Path(tmp) / "config.json"
    original_config_file = backend.CONFIG_FILE
    backend.CONFIG_FILE = temp_config
    env = {
        "OPENAI_API_KEY": MAIN_SECRET,
        "NER_API_KEY": NER_SECRET,
        "EMBEDDING_API_KEY": EMBED_SECRET,
        "DETECT_API_KEY": DETECT_SECRET,
    }
    try:
        with patch.dict(os.environ, env, clear=False):
            from_env = backend.default_config()
    finally:
        backend.CONFIG_FILE = original_config_file
    ck("all four API keys can be supplied through environment variables", (
        from_env.api_key == MAIN_SECRET
        and from_env.ner_api_key == NER_SECRET
        and from_env.embedding_api_key == EMBED_SECRET
        and from_env.detect_api_key == DETECT_SECRET
        and not from_env.ner_use_main
        and not from_env.embedding_use_main
        and not from_env.detect_use_main
    ), from_env)


passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
