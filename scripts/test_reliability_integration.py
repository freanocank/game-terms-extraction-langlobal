#!/usr/bin/env python3
"""Offline native-pipeline resume/export and evaluation integration tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import cli
import core.main as main
import ui.ui_backend as backend
from core.checkpoint import load as checkpoint_load
from core.evaluation import replay_extraction, score_terms
from core.responses import PipelineIncompleteError
from scripts import eval as evaluation


PROFILE = {"term_categories": ["UI系统"], "address_suffixes": []}


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")], model="m", usage=None)


class IntegrationTests(unittest.TestCase):
    def test_completed_checkpoint_materializes_full_replay_in_new_output_directory(self):
        self.run_fixture()
        output = self.root / "resume-output"
        with patch.object(main, "load_config", return_value=PROFILE), patch.object(main, "_ner_scan") as ner:
            resumed = main.run_pipeline(str(self.source), str(self.glossary), api_key="test-key",
                                        base_url="https://example.invalid/v1", model="m", opts=self.opts,
                                        checkpoint_dir=str(self.root / "checkpoint"), output_dir=str(output),
                                        raw_dir=str(output / "raw_data"))
            replayed = replay_extraction(output / "raw_data/extraction", self.source, self.glossary, "unused", self.opts)
        ner.assert_not_called()
        self.assertEqual(replayed[0]["translation"], resumed[0]["translation"])
        self.assertEqual(len(list((output / "raw_data/extraction").glob("b*_v*_r*.json"))), 3)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        network = patch("socket.socket.connect", side_effect=AssertionError("Offline suite attempted network access"))
        network.start()
        self.addCleanup(network.stop)
        self.source = self.root / "original.xlsx"
        self.glossary = self.root / "glossary.xlsx"
        self.glossary.write_bytes(main.empty_glossary_bytes())
        pd.DataFrame({"ID": ["phrase", "exact", "duplicate"], "ZH": ["制作完成", "制作", "制作"],
                      "EN": ["Production complete", "Craft", "Craft"]}).to_excel(self.source, index=False)
        self.opts = main.PipelineOpts(src_col=1, src_target_col=2, key_col=0, bilingual=True, no_translate=True, max_concurrent=2)

    def run_fixture(self, failed_first=False):
        calls, clients = [], []
        failed = False
        async def request(**kwargs):
            nonlocal failed
            calls.append(kwargs)
            if failed_first and kwargs["temperature"] == 0.3 and not failed:
                failed = True
                return reply("<html>upstream unavailable</html>")
            return reply('{"terms": [{"term": "制作", "row_id": "row-3", "target_term": "Craft", "category": "UI系统"}]}')
        def factory(**kwargs):
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=request)), close=AsyncMock())
            clients.append(client)
            return client
        def run():
            return main.run_pipeline(str(self.source), str(self.glossary), api_key="test-key",
                                     base_url="https://example.invalid/v1", model="m", opts=self.opts,
                                     checkpoint_dir=str(self.root / "checkpoint"), output_dir=str(self.root / "output"),
                                     raw_dir=str(self.root / "output" / "raw_data"))
        with patch.object(main, "load_config", return_value=PROFILE), patch.object(main, "_ner_scan", return_value=([], [])), patch("core.llm_extractor.AsyncOpenAI", side_effect=factory):
            if failed_first:
                with self.assertRaises(PipelineIncompleteError):
                    run()
                self.assertEqual(checkpoint_load(str(self.root / "checkpoint"))["chunk_idx"], 0)
                self.assertEqual(len(calls), 3)
            result = run()
        return result, calls, clients

    def test_resume_only_requests_failed_vote(self):
        result, calls, clients = self.run_fixture(failed_first=True)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result[0]["translation"], "Craft")
        self.assertEqual(result[0]["source_key"], "exact")
        self.assertEqual(result[0]["source_occurrences"][0]["file"], "original.xlsx")
        self.assertEqual([o["key"] for o in result[0]["source_occurrences"]], ["exact", "duplicate"])
        for client in clients:
            client.close.assert_awaited_once()

    def test_native_export_contract(self):
        result, _, _ = self.run_fixture()
        paths = main.save_outputs(result, self.root / "delivery")
        self.assertEqual(len(list((self.root / "delivery").iterdir())), 2)
        with pd.ExcelFile(paths[0]) as workbook:
            self.assertEqual(workbook.sheet_names, ["完整结果"])
        with pd.ExcelFile(paths[1]) as workbook:
            self.assertEqual(workbook.sheet_names, ["术语表"])
        template = pd.read_excel(paths[1])
        self.assertEqual(list(template.columns), main.TEMPLATE_COLUMNS)
        self.assertEqual(template.iloc[0]["审核状态"], "未审核")
        self.assertEqual(template.iloc[0]["Key值"], "exact")

    def test_complete_response_replay(self):
        result, _, _ = self.run_fixture()
        with patch.object(main, "load_config", return_value=PROFILE):
            replayed = replay_extraction(self.root / "output/raw_data/extraction", self.source, self.glossary, "unused", self.opts)
        self.assertEqual([(t["term"], t["translation"], t["source_key"]) for t in replayed],
                         [(t["term"], t["translation"], t["source_key"]) for t in result])

    def test_replay_rejects_wrong_source(self):
        self.run_fixture()
        pd.DataFrame({"ID": [1], "ZH": ["另外的文本"], "EN": ["Other text"]}).to_excel(self.source, index=False)
        with patch.object(main, "load_config", return_value=PROFILE), self.assertRaises(ValueError):
            replay_extraction(self.root / "output/raw_data/extraction", self.source, self.glossary, "unused", self.opts)

    def test_replay_rejects_missing_vote(self):
        self.run_fixture()
        (self.root / "output/raw_data/extraction/b0_v2_r0.json").unlink()
        with patch.object(main, "load_config", return_value=PROFILE), self.assertRaises(PipelineIncompleteError):
            replay_extraction(self.root / "output/raw_data/extraction", self.source, self.glossary, "unused", self.opts)

    def test_incompatible_resume_leaves_original_metadata_untouched(self):
        self.run_fixture()
        metadata = self.root / "checkpoint/run_meta.json"
        original = metadata.read_bytes()
        with patch.object(main, "load_config", return_value=PROFILE), self.assertRaises(ValueError):
            main.run_pipeline(str(self.source), str(self.glossary), model="changed-model", opts=self.opts,
                              output_dir=str(self.root / "new-output"), checkpoint_dir=str(self.root / "checkpoint"))
        self.assertEqual(metadata.read_bytes(), original)

    def test_ui_failure_does_not_export_or_clear_checkpoint(self):
        checkpoint = self.root / "checkpoint"
        checkpoint.mkdir()
        marker = checkpoint / "marker"
        marker.write_text("keep", encoding="utf-8")
        task = backend.ProcessingTask()
        with patch.object(backend, "ROOT", self.root), patch.object(main, "run_pipeline", side_effect=PipelineIncompleteError("提取", ["c0"])), patch.object(main, "save_outputs") as save:
            task._run(str(self.source), str(self.glossary), backend.RunConfig(api_key="test-key"), None, checkpoint_dir=str(checkpoint))
        save.assert_not_called()
        self.assertTrue(marker.exists())
        self.assertEqual(task.stage, "incomplete")
        self.assertIsNotNone(task.error)

    def test_cli_failure_does_not_export_or_clear_checkpoint(self):
        checkpoint = self.root / "checkpoint"
        checkpoint.mkdir()
        with patch.object(cli, "run_pipeline", side_effect=PipelineIncompleteError("提取", ["c0"])), patch.object(cli, "save_outputs") as save, self.assertRaises(PipelineIncompleteError):
            cli.main(["--source", str(self.source), "--api-key", "test-key", "--checkpoint", str(checkpoint), "--output", str(self.root / "cli-output")])
        save.assert_not_called()
        self.assertTrue(checkpoint.exists())

    def test_offline_score_does_not_require_credentials(self):
        result = self.root / "results.json"
        result.write_text(json.dumps([{"term": "制作", "translation": "Craft"}]), encoding="utf-8")
        gold = self.root / "gold.xlsx"
        pd.DataFrame({"term": ["制作", "淬锋玉"], "translation": ["Craft", "Jade"]}).to_excel(gold, index=False)
        with patch.dict("os.environ", {}, clear=True):
            report = evaluation.main(["--results", str(result), "--manual", str(gold), "--manual-target-col", "1", "--output", str(self.root / "evaluation")])
        self.assertEqual((report["tp"], report["fp"], report["fn"]), (1, 0, 1))
        self.assertEqual(report["translation_accuracy"], 1.0)
        self.assertEqual(report["mode"], "offline-results")

    def test_scoring_deduplicates_terms_and_treats_blank_translation_as_wrong(self):
        report = score_terms([{"term": "Moonkeep", "translation": None}, {"term": "moonkeep", "translation": "wrong"}], [{"term": "Moonkeep", "translation": "Mondfeste"}])
        self.assertEqual(report["predicted_count"], 1)
        self.assertEqual(report["translation_accuracy"], 0)


if __name__ == "__main__":
    unittest.main()
