#!/usr/bin/env python3
"""Offline contract: 3 distinct votes, row provenance, no silent completion."""
import asyncio
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import core.main as main
import core.header_detect as headers
import ui.ui_backend as backend
from core.checkpoint import load as load_checkpoint
from core.llm_extractor import LLMExtractor
from core.llm_translator import TermTranslator
from core.responses import PipelineIncompleteError, ResponseValidationError, chat_content, json_object, parse_entities, validate_terms
from core.retry import retry_async
from core.run_manifest import extraction_manifest, validate_resume
from core.source_records import prepare_source_records, unique_records, bind_context, records_from_texts, text_span
from core.voting import ensure_complete, collect_llm_votes, count_llm_votes, consolidate_terms


def reply(content='{"terms": []}', finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)], model="m", usage=None)


def response(terms=None, chunk="c0", error=None):
    return {"custom_id": chunk, "source_file": chunk, "extracted_terms": {"terms": terms or []}, **({"error": error} if error else {})}


class ResponseTests(unittest.TestCase):
    def test_prompt_example_is_valid_json_with_row_ids(self):
        from core.prompt_base import build_user_prompt
        prompt = build_user_prompt({}, "[row-2] 墨门", include_context=False)
        example, _ = json.JSONDecoder().raw_decode(prompt.split("输出格式（JSON）：\n", 1)[1])
        self.assertTrue(all("row_id" in term for term in example["terms"]))

    def test_ner_failure_is_logged_as_degraded_and_closes_client(self):
        from core.ner import ner_scan
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=reply("{}")))), close=AsyncMock())
        with patch("core.ner.AsyncOpenAI", return_value=client), self.assertLogs("pipeline", level="WARNING") as logs:
            self.assertEqual(ner_scan(["墨门"], "test-key", "https://example.invalid/v1", "m"), ([], []))
        self.assertIn("降级", " ".join(logs.output))
        client.close.assert_awaited_once()

    def test_native_translator_records_failure_and_closes_client(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=reply("{}")))), close=AsyncMock())
        with patch("core.llm_translator.AsyncOpenAI", return_value=client):
            translator = TermTranslator("test-key", "https://example.invalid/v1")
            self.assertEqual(translator.translate_batches([("system", "user")]), {})
        self.assertEqual(len(translator.last_errors), 1)
        client.close.assert_awaited_once()

    def test_log_formatter_redacts_runtime_secret(self):
        import logging
        from core.logger import RedactingFormatter
        record = logging.LogRecord("pipeline", logging.ERROR, "", 0, "failed %s", ("test-key",), None)
        formatted = RedactingFormatter("%(message)s", ["test-key"]).format(record)
        self.assertEqual(formatted, "failed ***")

    def test_legal_empty_terms(self):
        self.assertEqual(validate_terms(json_object(chat_content(reply()))), {"terms": []})

    def test_invalid_chat_envelopes(self):
        for value in ("<html>upstream unavailable</html>", SimpleNamespace(choices=[]), reply(""), reply("{}", "length")):
            with self.subTest(value=value), self.assertRaises(ResponseValidationError):
                chat_content(value)

    def test_invalid_terms_schema(self):
        for value in ({}, {"terms": None}, {"terms": ["墨门"]}, {"terms": [{"term": 12}]}, {"terms": [{"term": "墨门", "row_id": 2}]}):
            with self.subTest(value=value), self.assertRaises(ResponseValidationError):
                validate_terms(value)

    def test_shared_json_parser(self):
        for raw in ('```json\n{"terms": []}\n```', json.dumps('{"terms": []}')):
            self.assertEqual(json_object(raw), {"terms": []})

    def test_ner_requires_arrays(self):
        with self.assertRaises(ResponseValidationError):
            parse_entities('{"persons": "墨门"}')

    def test_translator_schema(self):
        for raw in ("<html>bad gateway</html>", '{"translations": ["bad"]}', '{}'):
            with self.subTest(raw=raw), self.assertRaises(ResponseValidationError):
                TermTranslator._parse(raw)

    def test_blank_translation_is_not_success(self):
        self.assertEqual(TermTranslator._parse('{"translations": [{"term": "墨门", "translation": " "}]}'), {})

    def test_nonretryable_errors(self):
        auth = RuntimeError("unauthorized")
        auth.status_code = 401
        for error in (ResponseValidationError("bad schema"), ValueError("bad config"), auth):
            call = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                asyncio.run(retry_async(call, initial_delay=0))
            self.assertEqual(call.await_count, 1)

    def test_bounded_network_retry(self):
        call = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), "ok"])
        self.assertEqual(asyncio.run(retry_async(call, max_attempts=3, initial_delay=0)), "ok")
        self.assertEqual(call.await_count, 3)

    def test_connection_check_rejects_invalid_content(self):
        for bad in (SimpleNamespace(choices=[]), "<html>upstream unavailable</html>"):
            client = Mock()
            client.chat.completions.create.return_value = bad
            with patch("openai.OpenAI", return_value=client):
                ok, _ = backend.test_api_connection(backend.RunConfig(api_key="test-key"))
            self.assertFalse(ok)
            client.close.assert_called_once()


class VoteTests(unittest.TestCase):
    def test_duplicate_in_one_round(self):
        term = {"term": "墨门", "category": "门派"}
        raw = [[response([term] * 3)], [response()], [response()]]
        self.assertEqual(count_llm_votes(collect_llm_votes(raw)), [])

    def test_overlap_does_not_add_votes(self):
        term = {"term": "墨门", "row_id": "row-2"}
        raw = [[response([term], f"c{i}") for i in range(3)], [], []]
        self.assertEqual(count_llm_votes(collect_llm_votes(raw)), [])

    def test_rows_cannot_pool_votes(self):
        self.assertEqual(count_llm_votes([{"term": "墨门", "row_id": f"row-{i + 2}", "_vote": i} for i in range(3)]), [])

    def test_two_votes_still_fail_gate(self):
        self.assertEqual(count_llm_votes([{"term": "墨门", "_vote": i} for i in range(2)]), [])

    def test_consensus_is_order_independent(self):
        votes = [{"term": "墨门", "row_id": "row-2", "category": "门派", "target_term": target, "_vote": i}
                 for i, target in enumerate(("Mo Sect", "Momen", "Momen"))]
        result = count_llm_votes(votes)
        self.assertEqual(result, count_llm_votes(list(reversed(votes))))
        self.assertEqual(result[0]["target_term"], "Momen")

    def test_failures_and_missing_responses_block_completion(self):
        cases = ([[response(error="timeout")] for _ in range(3)], [[response()], [], [response()]], [[response()]])
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(PipelineIncompleteError):
                ensure_complete(raw, 1)

    def test_duplicate_chunk_response(self):
        with self.assertRaises(PipelineIncompleteError):
            ensure_complete([[response(), response()] for _ in range(3)], 2)


class ProvenanceTests(unittest.TestCase):
    def test_numeric_text_is_preserved_but_integer_key_is_normalized(self):
        frame = pd.DataFrame({"ID": [1.0], "ZH": [2.0], "EN": [3.0]})
        record = prepare_source_records(frame, main.PipelineOpts(src_col=1, src_target_col=2, key_col=0, bilingual=True))[0]
        self.assertEqual((record.key, record.source, record.target), ("1", "2.0", "3.0"))

    def test_native_response_requires_row_id(self):
        raw = [[response([{"term": "墨门"}])] for _ in range(3)]
        with self.assertRaises(ResponseValidationError):
            collect_llm_votes(raw, {"c0": records_from_texts(["墨门"])})

    def test_consolidation_prefers_standalone_row_with_its_own_evidence(self):
        terms = [{"term": "制作", "row_id": row, "target_term": "Craft"} for row in ("row-2", "row-3")]
        bind_context(terms, records_from_texts(["ZH: 制作完成 | EN: Craft complete", "ZH: 制作 | EN: Craft"]))
        combined = consolidate_terms(terms)
        self.assertEqual(combined[0]["source_row_id"], "row-3")
        self.assertEqual(len(combined[0]["source_occurrences"]), 2)

    def test_raw_span_preserves_removed_markup_mapping(self):
        frame = pd.DataFrame({"ZH": [" <b>墨</b>门 "]})
        record = prepare_source_records(frame, main.PipelineOpts())[0]
        term = {"term": "墨门"}
        bind_context([term], [record])
        start, end = term["raw_source_span"]
        self.assertEqual(term["source_raw"][start:end], "墨</b>门")

    def test_precise_rows_beat_earlier_substrings(self):
        texts = ["ZH: 制作完成 | EN: Production complete", "ZH: 制作 | EN: Craft", "ZH: 往生极乐 | EN: Paradise",
                 "ZH: 极 | EN: Extreme", "ZH: 不安生 | EN:", "ZH: 姓名：安 | EN: Name: An"]
        terms = [{"term": name, "target_term": target} for name, target in (("制作", "Craft"), ("极", "Extreme"), ("安", "An"))]
        main._batch_match_context(terms, texts, [str(i) for i in range(6)])
        self.assertEqual([t["source_key"] for t in terms], ["1", "3", "5"])
        self.assertEqual([main._aligned_target_value(t, True) for t in terms], ["Craft", "Extreme", "An"])

    def test_explicit_row_never_migrates(self):
        term = {"term": "制作", "row_id": "row-2", "target_term": "Craft"}
        bind_context([term], records_from_texts(["ZH: 制作完成 | EN: Production complete", "ZH: 制作 | EN: Craft"]))
        self.assertEqual(term["source_row_id"], "row-2")
        self.assertEqual(main._aligned_target_value(term, True), "")

    def test_latin_name_can_touch_han_text(self):
        records = records_from_texts(["ZH: 她和Robin一起去见会长。 | EN: She and Robin visit the leader."])
        reply = response([{"term": "Robin", "row_id": "row-2", "target_term": "Robin"}])
        [candidate] = count_llm_votes(collect_llm_votes([[reply] for _ in range(3)], {"c0": records}))

        self.assertEqual(candidate["term"], "Robin")
        bind_context([candidate], records)
        self.assertEqual(candidate["source_span"], (2, 7))
        self.assertEqual(main._aligned_target_value(candidate, True), "Robin")

    def test_invalid_word_fragment_is_not_a_valid_empty_round(self):
        records = records_from_texts(["她和Robinson一起去见会长。"])
        invalid = response([{"term": "Robin", "row_id": "row-2"}])
        with self.assertRaisesRegex(ResponseValidationError, "未命中指定来源行"):
            collect_llm_votes([[invalid]], {"c0": records})
        self.assertEqual(collect_llm_votes([[response([])]], {"c0": records}), [])

    def test_unknown_row_id(self):
        raw = [[response([{"term": "墨门", "row_id": "row-999"}])] for _ in range(3)]
        with self.assertRaises(ResponseValidationError):
            collect_llm_votes(raw, {"c0": records_from_texts(["墨门"])})

    def test_target_word_boundaries_and_case(self):
        term = {"term": "术", "target_term": "art", "source_text": "ZH: 术 | EN: Martial"}
        self.assertEqual(main._aligned_target_value(term, True), "")
        term["source_text"] = "ZH: 术 | EN: Sword Art"
        self.assertEqual(main._aligned_target_value(term, True), "Art")

    def test_source_word_boundaries(self):
        self.assertIsNone(text_span("art", "Martial"))
        self.assertEqual(text_span("Moonkeep", "Enter Moonkeep!"), (6, 14))

    def test_duplicate_keys_and_raw_text_preserved(self):
        frame = pd.DataFrame({"ID": ["a", "b", "c"], "ZH": ["<b>墨门</b>", "火神", "墨门"], "EN": ["Momen", "Fire God", "Momen"]})
        records = prepare_source_records(frame, main.PipelineOpts(src_col=1, src_target_col=2, key_col=0, bilingual=True))
        self.assertEqual([r.source for r in unique_records(records)], ["墨门", "火神"])
        term = {"term": "墨门", "row_id": "row-2"}
        bind_context([term], records)
        self.assertEqual([o["key"] for o in term["source_occurrences"]], ["a", "c"])
        self.assertEqual(term["source_raw"], "<b>墨门</b>")


class ExtractorTests(unittest.TestCase):
    def test_provider_error_does_not_persist_key(self):
        with tempfile.TemporaryDirectory() as directory:
            extractor, _ = self.make_extractor(directory, AsyncMock(side_effect=RuntimeError("rejected test-key")))
            async def run():
                return await extractor._process_single_text_async("x", "id", asyncio.Semaphore(1), "s", "u", "m", 0, 100, "c0")
            result = extractor.run_sync(run())
            self.assertNotIn("test-key", result["error"])
            self.assertNotIn("test-key", extractor._exchange_log_file.read_text(encoding="utf-8"))

    def test_progress_failure_cancels_outstanding_requests(self):
        active = 0
        async def request(**kwargs):
            nonlocal active
            active += 1
            try:
                await asyncio.sleep(0.005)
                return reply()
            finally:
                active -= 1
        def cancel(*args):
            raise RuntimeError("user cancelled")
        with tempfile.TemporaryDirectory() as directory:
            extractor, _ = self.make_extractor(directory, request)
            with self.assertRaises(RuntimeError):
                main._run_batch_votes(extractor, ["a", "b", "c"], "s", ["a", "b", "c"], "m", 100, 2,
                                      ["c0", "c1", "c2"], "b", progress_callback=cancel)
            self.assertEqual(active, 0)

    def test_invalid_provenance_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            call = AsyncMock(side_effect=[reply('{"terms": [{"term": "墨门", "row_id": "row-999"}]}'),
                                         reply('{"terms": [{"term": "墨门", "row_id": "row-2"}]}')])
            extractor, _ = self.make_extractor(directory, call)
            async def run():
                args = ("[row-2] 墨门", "id", asyncio.Semaphore(1), "system", "prompt", "model", 0, 100, "c0")
                self.assertIn("error", await extractor._process_single_text_async(*args, records=records_from_texts(["墨门"])))
                self.assertNotIn("error", await extractor._process_single_text_async(*args, records=records_from_texts(["墨门"])))
            extractor.run_sync(run())
            self.assertEqual(call.await_count, 2)

    def make_extractor(self, directory, call):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call)), close=AsyncMock())
        with patch("core.llm_extractor.AsyncOpenAI", return_value=client):
            extractor = LLMExtractor("test-key", "https://example.invalid/v1", directory, directory)
        self.addCleanup(extractor.close)
        return extractor, client

    def test_request_content_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            call = AsyncMock(return_value=reply())
            extractor, _ = self.make_extractor(directory, call)
            async def run():
                semaphore = asyncio.Semaphore(1)
                async def one(text="old", model="old-model", prompt="old prompt", temperature=0):
                    return await extractor._process_single_text_async(text, "same-id", semaphore, "system", prompt, model, temperature, 100, "c0")
                await one()
                await one()
                self.assertEqual(call.await_count, 1)
                await one(text="new")
                await one(model="new-model")
                await one(prompt="new prompt")
                await one(temperature=0.7)
            extractor.run_sync(run())
            self.assertEqual(call.await_count, 5)

    def test_failures_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            call = AsyncMock(side_effect=[reply("not JSON"), reply()])
            extractor, _ = self.make_extractor(directory, call)
            async def run():
                args = ("text", "id", asyncio.Semaphore(1), "system", "prompt", "model", 0, 100, "c0")
                self.assertIn("error", await extractor._process_single_text_async(*args))
                self.assertNotIn("error", await extractor._process_single_text_async(*args))
            extractor.run_sync(run())
            self.assertEqual(call.await_count, 2)

    def test_actual_concurrency_and_loop_lifetime(self):
        active = peak = 0
        loops = []
        async def request(**kwargs):
            nonlocal active, peak
            loops.append(asyncio.get_running_loop())
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.002)
            active -= 1
            return reply()
        with tempfile.TemporaryDirectory() as directory:
            extractor, client = self.make_extractor(directory, request)
            for suffix in ("a", "b"):
                main._run_batch_votes(extractor, ["a", "b", "c"], suffix, ["a", "b", "c"], "m", 100, 2, ["c0", "c1", "c2"], suffix)
            self.assertEqual(peak, 2)
            self.assertEqual(len({id(loop) for loop in loops}), 1)
            self.doCleanups()
            client.close.assert_awaited_once()


class PipelineTests(unittest.TestCase):
    def test_failed_chunk_keeps_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "_ner_scan", return_value=([], [])), patch.object(main, "_run_batch_votes", return_value=[[response(error="timeout")] for _ in range(3)]):
            checkpoint = str(Path(directory) / "checkpoint")
            with self.assertRaises(PipelineIncompleteError):
                main.extract_terms(["墨门"], {}, "test-key", "https://example.invalid/v1", "model", output_dir=directory, checkpoint_dir=checkpoint, opts=main.PipelineOpts(max_concurrent=1))
            state = load_checkpoint(checkpoint)
            self.assertEqual((state["chunk_idx"], state["status"], state["terms"]), (0, "incomplete", []))

    def test_empty_success_completes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "_ner_scan", return_value=([], [])), patch.object(main, "_run_batch_votes", return_value=[[response()] for _ in range(3)]):
            checkpoint = str(Path(directory) / "checkpoint")
            terms = main.extract_terms(["没有术语"], {}, "test-key", "https://example.invalid/v1", "model", output_dir=directory, checkpoint_dir=checkpoint)
            self.assertEqual(terms, [])
            self.assertEqual(load_checkpoint(checkpoint)["chunk_idx"], 1)

    def test_results_independent_of_batch_concurrency(self):
        def votes(extractor, chunks, system, prompts, model, tokens, concurrent, files, batch_id, **kwargs):
            responses = [response([{"term": "墨门", "row_id": row} for row in re.findall(r"\[(row-\d+)\]", chunk)], file) for chunk, file in zip(chunks, files)]
            return [responses, responses, responses]
        outputs = []
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "_ner_scan", return_value=([], [])), patch.object(main, "_run_batch_votes", side_effect=votes):
            for concurrent in (1, 2, 4):
                outputs.append(main.extract_terms([f"墨门弟子第{i}句" for i in range(5)], {}, "test-key", "https://example.invalid/v1", "model", output_dir=directory, target_chars=20, opts=main.PipelineOpts(max_concurrent=concurrent)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_missing_translations_retry_only_missing(self):
        translator = Mock()
        translator.translate_batches.side_effect = [{"墨门": "Momen"}, {"淬锋玉": "Jade"}]
        with patch.object(main, "TermTranslator", return_value=translator):
            output = main.match_and_translate([{"term": "墨门"}, {"term": "淬锋玉"}], {}, {}, "", "", "model")
        self.assertTrue(all(t["translation"] for t in output))
        self.assertNotIn("墨门", translator.translate_batches.call_args.args[0][0][1])

    def test_unresolved_translation_blocks_success(self):
        translator = Mock()
        translator.translate_batches.return_value = {}
        with patch.object(main, "TermTranslator", return_value=translator), self.assertRaises(PipelineIncompleteError):
            main.match_and_translate([{"term": "墨门"}], {}, {}, "", "", "model")
        self.assertEqual(translator.translate_batches.call_count, 2)

    def test_partial_translation_cache_survives_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            translator = Mock()
            translator.translate_batches.side_effect = [{"墨门": "Momen"}, {}]
            with patch.object(main, "TermTranslator", return_value=translator), self.assertRaises(PipelineIncompleteError):
                main.match_and_translate([{"term": "墨门"}, {"term": "淬锋玉"}], {}, {}, "", "", "model", checkpoint_dir=directory)
            translator.translate_batches.side_effect = [{"淬锋玉": "Jade"}]
            with patch.object(main, "TermTranslator", return_value=translator):
                output = main.match_and_translate([{"term": "墨门"}, {"term": "淬锋玉"}], {}, {}, "", "", "model", checkpoint_dir=directory)
            self.assertEqual(len(output), 2)
            self.assertNotIn("墨门", translator.translate_batches.call_args.args[0][0][1])

    def test_embedding_reference_case(self):
        translator, store = Mock(), Mock()
        translator.translate_batches.return_value = {"Moonkeep Gate": "Tor"}
        store.search.return_value = [("Moonkeep", 0.99)]
        with patch.object(main, "TermTranslator", return_value=translator):
            output = main.match_and_translate([{"term": "Moonkeep Gate"}], {"moonkeep": ("Moonkeep", "Mondfeste")}, {}, "", "", "model", embed_store=store, source_language="en", target_language="de")
        self.assertEqual(output[0]["ref_trans"], "Mondfeste")


class MappingAndManifestTests(unittest.TestCase):
    def test_manual_mapping_beats_multilingual_rules(self):
        frame = pd.DataFrame({"ID": [1], "EN-v2": ["Moonkeep"], "DE-MT": ["Maschine"], "Approved": ["Mondfeste"]})
        settings = {"source_language": "en", "target_language": "de", "base_url": "https://example.invalid/v1", "model": "m"}
        with tempfile.TemporaryDirectory() as directory, patch.object(headers, "CACHE_FILE", Path(directory) / "cache.json"):
            headers.record_source_correction(frame.columns, 1, 0, 3, df=frame, **settings)
            self.assertEqual(headers.detect_source_column(frame, api_key="", **settings)["en_col"], 3)
            headers.record_glossary_correction(frame.columns, 1, 3, df=frame, **settings)
            self.assertEqual(headers.detect_glossary_columns(frame, api_key="", **settings)["en_col"], 3)

    def test_changed_manifest_rejected_without_mutation(self):
        saved = {"extraction_manifest": {"model": "old"}}
        with self.assertRaises(ValueError):
            validate_resume(saved, {"chunk_idx": 1}, {"model": "new"})
        self.assertEqual(saved, {"extraction_manifest": {"model": "old"}})

    def test_legacy_checkpoint_requires_new_run(self):
        with self.assertRaises(ValueError):
            validate_resume({}, {"chunk_idx": 1}, {"version": 2})

    def test_translation_and_concurrency_changes_reuse_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.xlsx"
            source.write_bytes(b"fixture")
            a = extraction_manifest(source, {}, {}, main.PipelineOpts(), "https://example.invalid/v1", "m")
            b = extraction_manifest(source, {}, {}, main.PipelineOpts(no_translate=True, max_concurrent=2), "https://example.invalid/v1", "m")
            self.assertEqual(a, b)


if __name__ == "__main__":
    with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access in offline suite")):
        unittest.main()
