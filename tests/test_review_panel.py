"""审稿执行与版本归属的离线回归；不会调用任何模型。"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_review_panel as panel


class ReviewPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        (self.task / "draft.md").write_text("3家店试用，1家付费300元。", encoding="utf-8")
        (self.task / "source.md").write_text("SOURCE_ONLY_FOR_5：3家试用，1家付费300元。", encoding="utf-8")
        (self.task / "changes.md").write_text("第1句‘1家付费300元’，核付费数量与金额。", encoding="utf-8")

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["review", "--task-dir", str(self.task), "--draft", "draft.md", *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                panel.main()
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        result = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None
        return code, result

    def run_one(self, output):
        return panel.run_one("codex", self.task, "test", "new prompt", output, 1, "xhigh", None)

    def test_old_output_is_rejected_without_starting_a_process(self):
        output = self.task / "review.md"
        output.write_text("OLD_REVIEW", encoding="utf-8")
        with patch.object(panel.subprocess, "run") as run:
            result = self.run_one(output)
        self.assertEqual(result["error"], "output_exists")
        self.assertEqual(output.read_text(), "OLD_REVIEW")
        run.assert_not_called()

    def test_success_without_new_output_fails(self):
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(
                panel.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            result = self.run_one(self.task / "new.md")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["output"])

    def test_fresh_file_is_accepted(self):
        output = self.task / "new.md"

        def respond(*args, **kwargs):
            output.write_text("本轮意见", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")

        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(panel.subprocess, "run", side_effect=respond):
            result = self.run_one(output)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["output"]).read_text(), "本轮意见")

    def test_claude_stdout_is_saved_as_this_runs_output(self):
        with patch.object(panel, "command", return_value=(["mock"], True)), patch.object(
                panel.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "fresh stdout", "")):
            result = self.run_one(self.task / "new.md")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["output"]).read_text(), "fresh stdout")

    def test_nonzero_exit_is_not_success_even_if_output_was_written(self):
        output = self.task / "new.md"

        def respond(*args, **kwargs):
            output.write_text("partial", encoding="utf-8")
            return subprocess.CompletedProcess([], 1, "", "failure")

        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(panel.subprocess, "run", side_effect=respond):
            result = self.run_one(output)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["output"])

    def test_timeout_is_recorded(self):
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(
                panel.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock", 1)):
            result = self.run_one(self.task / "new.md")
        self.assertEqual(result["status"], "timeout")

    def test_missing_cli_has_failed_summary(self):
        with patch.object(panel.shutil, "which", return_value=None), patch.object(panel.subprocess, "run") as run:
            code, summary = self.invoke("--engine", "claude", "--groups", "10")
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["results"][0]["error"], "FileNotFoundError")
        self.assertEqual(json.loads((Path(summary["review_dir"]) / "summary.json").read_text()), summary)
        run.assert_not_called()

    def test_spawn_error_has_failed_summary(self):
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(
                panel.subprocess, "run", side_effect=PermissionError("cannot start")):
            code, summary = self.invoke("--engine", "codex", "--groups", "10")
        self.assertEqual(code, 1)
        self.assertEqual(summary["results"][0]["error"], "PermissionError")

    def test_unexpected_worker_error_does_not_drop_other_results(self):
        def worker(engine, task_dir, name, prompt, out_file, timeout, effort, model):
            if name.startswith("4-"):
                raise RuntimeError("simulated worker error")
            return {"group": name, "status": "ok", "output": str(out_file)}

        with patch.object(panel, "run_one", side_effect=worker):
            code, summary = self.invoke("--engine", "codex", "--groups", "4,10")
        self.assertEqual(code, 1)
        self.assertEqual(len(summary["results"]), 2)
        self.assertEqual({r["status"] for r in summary["results"]}, {"ok", "failed"})

    def test_same_filename_keeps_distinct_runs_and_input_fingerprints(self):
        code1, first = self.invoke("--prompts-only", "--groups", "10")
        original_summary = (Path(first["review_dir"]) / "summary.json").read_bytes()
        (self.task / "draft.md").write_text("改后的另一版。", encoding="utf-8")
        code2, second = self.invoke("--prompts-only", "--groups", "10")
        self.assertEqual((code1, code2), (0, 0))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["draft_sha256"], second["draft_sha256"])
        for summary in [first, second]:
            saved = (Path(summary["review_dir"]) / "inputs/draft.md").read_text()
            self.assertEqual(panel.fingerprint(saved), summary["draft_sha256"])
        self.assertEqual((Path(first["review_dir"]) / "summary.json").read_bytes(), original_summary)

    def test_local_scope_requires_explicit_groups_and_changes(self):
        for args in [("--scope", "local"), ("--scope", "local", "--groups", "5")]:
            with self.subTest(args=args), patch.object(panel.subprocess, "run") as run:
                code, _ = self.invoke("--prompts-only", *args)
            self.assertEqual(code, 2)
            run.assert_not_called()
        self.assertFalse((self.task / "review").exists())

    def test_local_fact_check_only_creates_selected_group(self):
        with patch.object(panel.subprocess, "run") as run:
            code, summary = self.invoke("--prompts-only", "--scope", "local", "--groups", "5", "--changes", "changes.md")
        self.assertEqual(code, 0)
        self.assertEqual(summary["groups"], ["5-让人信"])
        self.assertEqual(summary["status"], "prompts_only")
        self.assertEqual(summary["results"], [])
        directory = Path(summary["review_dir"])
        self.assertEqual(len(list((directory / "prompts").glob("*.md"))), 1)
        prompt = (directory / "prompts/5-让人信.md").read_text()
        self.assertIn((self.task / "changes.md").read_text(), prompt)
        self.assertIn("SOURCE_ONLY_FOR_5", prompt)
        run.assert_not_called()

    def test_local_comprehension_does_not_receive_source(self):
        code, summary = self.invoke("--prompts-only", "--scope", "local", "--groups", "4,10", "--changes", "changes.md")
        self.assertEqual(code, 0)
        self.assertNotIn("source", summary["input_sha256"])
        for prompt in (Path(summary["review_dir"]) / "prompts").glob("*.md"):
            self.assertNotIn("SOURCE_ONLY_FOR_5", prompt.read_text())

    def test_full_scope_keeps_all_reviewers(self):
        code, summary = self.invoke("--prompts-only")
        self.assertEqual(code, 0)
        self.assertEqual(len(summary["groups"]), 9)
        self.assertIn("10-用意", summary["groups"])


if __name__ == "__main__":
    unittest.main()
