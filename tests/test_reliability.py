"""Synthetic, offline reliability regressions; no private files or providers."""
import contextlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_review_panel as panel
import run_first_reader as reader


class OfflineCase(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                       "socket.getaddrinfo", "subprocess.Popen"):
            stack.enter_context(patch(target, side_effect=AssertionError("offline test boundary: " + target)))
        stack.enter_context(patch.object(panel, "codex_model", side_effect=AssertionError("no user config")))
        self.key_mock = stack.enter_context(patch.object(reader, "read_key", return_value=("synthetic-key", "mock")))
        self.post_mock = stack.enter_context(patch.object(reader, "post_once", side_effect=AssertionError("no real provider")))
        stack.enter_context(patch.dict(reader.os.environ, {"STORY_CRAFT_READER_BUDGET": ""}))
        self.temp = stack.enter_context(tempfile.TemporaryDirectory())
        self.base = Path(self.temp)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.task = self.base / "task"
        self.task.mkdir()
        (self.task / "draft.md").write_text("Synthetic draft.", encoding="utf-8")
        (self.task / "source.md").write_text("SOURCE_SENTINEL", encoding="utf-8")
        (self.task / "changes.md").write_text("Only sentence one.", encoding="utf-8")
        revision = self.repo / "references/revision"
        revision.mkdir(parents=True)
        for filename, prefix, end in [("structure.md", "p", 22), ("body.md", "m", 52)]:
            (revision / filename).write_text("\n\n".join('<a id="%s%02d"></a>\nSynthetic rule.' % (prefix, n)
                                                       for n in range(1, end + 1)), encoding="utf-8")
        (revision / "cases.md").write_text('| [C1](#c1) | example | 风险反例 |\n<a id="c1"></a>\nSynthetic case.', encoding="utf-8")
        (revision / "titles.md").write_text("Synthetic titles.", encoding="utf-8")
        agents = self.repo / "agents"
        agents.mkdir()
        (agents / "first-reader.md").write_text("<!-- reader-prompt:begin -->Read only this draft.<!-- reader-prompt:end -->", encoding="utf-8")
        stack.enter_context(patch.object(panel, "ROOT", self.repo))
        stack.enter_context(patch.object(panel, "REV", revision))
        stack.enter_context(patch.object(reader, "ROOT", self.repo))

    def invoke(self, *args, task=None, draft="draft.md"):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["review", "--task-dir", str(task or self.task), "--draft", draft, *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                panel.main()
            except SystemExit as exc:
                code = exc.code
                if isinstance(code, str):
                    stderr.write(code)
                    code = 1
            else:
                code = 0
        result = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None
        return code, result, stderr.getvalue()

    def assert_rejected_cleanly(self, *args, task=None, draft="draft.md"):
        before = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))
        with patch.object(panel, "run_one") as run:
            code, _, error = self.invoke(*args, task=task, draft=draft)
        self.assertNotEqual(code, 0, "unsafe input was accepted")
        self.assertTrue(error)
        self.assertNotIn("Traceback", error)
        self.assertEqual(before, sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*")))
        run.assert_not_called()


class PanelPreflightTests(OfflineCase):
    def test_repository_task_rejected(self):
        (self.repo / "draft.md").write_text("Synthetic draft.")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10", task=self.repo)

    def test_relative_task_rejected(self):
        with contextlib.chdir(self.task):
            self.assert_rejected_cleanly("--prompts-only", "--groups", "10", task=Path("."), draft=str(self.task / "draft.md"))

    def test_main_and_git_directories_protected_from_worktree(self):
        worktree = self.repo / ".airbuild/worktree/reliability"
        worktree.mkdir(parents=True)
        admin = self.repo / ".git/worktrees/reliability"
        admin.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: " + str(admin) + "\n")
        (admin / "commondir").write_text("../..\n")
        with patch.object(panel, "ROOT", worktree):
            for target in [self.repo, self.repo / ".git", admin]:
                with self.subTest(target=target):
                    self.assert_rejected_cleanly("--prompts-only", "--groups", "10", task=target,
                                                 draft=str(self.task / "draft.md"))

    def test_external_worktree_relative_git_metadata_protects_main(self):
        worktree = self.base / "separate-worktree"
        worktree.mkdir()
        admin = self.repo / ".git/worktrees/external"
        admin.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/external")
        (admin / "commondir").write_text("../..")
        with patch.object(panel, "ROOT", worktree):
            self.assert_rejected_cleanly("--prompts-only", "--groups", "10", task=self.repo,
                                         draft=str(self.task / "draft.md"))

    def test_prefix_neighbor_and_temporary_alias_allowed(self):
        neighbor = self.base / "repo-neighbor"
        neighbor.mkdir()
        alias = self.base / "temp-alias"
        alias.symlink_to(neighbor, target_is_directory=True)
        code, summary, _ = self.invoke("--prompts-only", "--groups", "10", task=alias,
                                       draft=str(self.task / "draft.md"))
        self.assertEqual(code, 0)
        self.assertTrue(Path(summary["review_dir"]).is_relative_to(neighbor))

    def test_review_symlink_rejected(self):
        (self.task / "review").symlink_to(self.repo, target_is_directory=True)
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_stem_symlink_rejected(self):
        (self.task / "review").mkdir()
        (self.task / "review/draft").symlink_to(self.base, target_is_directory=True)
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_dangling_output_symlinks_rejected(self):
        (self.task / "review").symlink_to(self.base / "missing", target_is_directory=True)
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_non_directory_output_rejected(self):
        (self.task / "review").write_text("do not replace")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_bad_late_manual_anchor_produces_no_copies(self):
        (panel.REV / "structure.md").write_text('<a id="p01"></a>\nOnly one anchor.')
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10,1")

    def test_missing_late_manual_produces_no_copies(self):
        (panel.REV / "body.md").unlink()
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10,4")

    def test_invalid_utf8_manual_produces_no_copies(self):
        (panel.REV / "body.md").write_bytes(b"\xff")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "4")

    def test_invalid_utf8_draft_produces_no_copies(self):
        (self.task / "draft.md").write_bytes(b"\xff")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_empty_draft_rejected(self):
        (self.task / "draft.md").write_text(" \n")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_empty_source_rejected(self):
        (self.task / "source.md").write_text(" \n")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "5")

    def test_invalid_utf8_source_rejected(self):
        (self.task / "source.md").write_bytes(b"\xff")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "5")

    def test_invalid_utf8_changes_rejected(self):
        (self.task / "changes.md").write_bytes(b"\xff")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10", "--scope", "local", "--changes", "changes.md")

    def test_invalid_arguments_rejected(self):
        for args in [("--timeout", "0"), ("--timeout", "-1"), ("--audience", ""), ("--medium", "  "),
                     ("--focus", ""), ("--groups", "99"), ("--groups", ""), ("--groups", "1,,2")]:
            with self.subTest(args=args):
                self.assert_rejected_cleanly("--prompts-only", "--groups", "10", *args)

    def test_source_not_read_by_unrelated_groups(self):
        (self.task / "source.md").write_bytes(b"\xff")
        code, summary, _ = self.invoke("--prompts-only", "--groups", "1,2,3,4,6,7,8,9,10", "--source", "nonexistent")
        self.assertEqual(code, 0)
        self.assertNotIn("source", summary["input_sha256"])
        self.assertFalse((Path(summary["review_dir"]) / "inputs/source.md").exists())

    def test_explicit_context_and_focus_routing(self):
        audience, medium, focus = "同行专家", "八分钟图文（逐页阅读）", "FOCUS_SENTINEL"
        code, summary, _ = self.invoke("--prompts-only", "--groups", "1,2,3,4,5,6,7,8,9,10",
                                       "--audience", audience, "--medium", medium, "--focus", focus)
        self.assertEqual(code, 0)
        for file in (Path(summary["review_dir"]) / "prompts").iterdir():
            prompt, group = file.read_text(), file.name.split("-")[0]
            self.assertIn(audience, prompt)
            self.assertIn(medium, prompt)
            self.assertEqual(focus in prompt, group in ["1", "2", "3", "9"])
            self.assertEqual("SOURCE_SENTINEL" in prompt, group == "5")
        self.assertEqual(summary["task_context"]["audience"], {"value": audience, "source": "explicit"})

    def test_defaults_identified_as_assumptions_not_confirmed_task(self):
        _, summary, _ = self.invoke("--prompts-only", "--groups", "10")
        for key in ["audience", "medium"]:
            self.assertEqual(summary["task_context"][key]["source"], "default_assumption")
        self.assertEqual(summary["execution"]["external_requests"], 0)
        self.assertEqual(summary["execution"]["reviews_received"], 0)
        self.assertEqual(summary["execution"]["draft_quality"], "not_assessed")

    def test_mkdir_failure_has_clear_error(self):
        with patch.object(panel.tempfile, "mkdtemp", side_effect=PermissionError("synthetic deny")):
            code, _, error = self.invoke("--prompts-only", "--groups", "10")
        self.assertNotEqual(code, 0)
        self.assertTrue(error)
        self.assertNotIn("Traceback", error)


    def test_dangling_stem_and_file_stem_rejected(self):
        (self.task / "review").mkdir()
        stem = self.task / "review/draft"
        stem.symlink_to(self.base / "absent")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")
        stem.unlink()
        stem.write_text("do not replace")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10")

    def test_bad_case_anchor_and_empty_title_manual_preflight(self):
        (panel.REV / "cases.md").write_text('| [C1](#c1) | example | 风险反例 |')
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10,5")
        (panel.REV / "titles.md").write_text(" ")
        self.assert_rejected_cleanly("--prompts-only", "--groups", "10,9")

    def test_missing_and_empty_changes_preflight(self):
        for value in [None, " "]:
            if value is None:
                (self.task / "changes.md").unlink()
            else:
                (self.task / "changes.md").write_text(value)
            self.assert_rejected_cleanly("--prompts-only", "--groups", "10", "--scope", "local", "--changes", "changes.md")

    def test_unrelated_task_files_not_discovered(self):
        for name in ["audience.md", "medium.md", "focus.md", "notes.md"]:
            (self.task / name).write_bytes(b"\xff")
        read = Path.read_text
        def guarded(path, *args, **kwargs):
            if path.name in ["audience.md", "medium.md", "focus.md", "notes.md", "source.md"]:
                raise AssertionError("unrequested task file read")
            return read(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded):
            code, _, _ = self.invoke("--prompts-only", "--groups", "10")
        self.assertEqual(code, 0)

    def test_all_prompts_prepared_before_first_mkdir(self):
        prepared = []
        original_build, original_mkdir = panel.build_prompt, Path.mkdir
        def build(*args, **kwargs):
            value = original_build(*args, **kwargs)
            prepared.append(value)
            return value
        def mkdir(path, *args, **kwargs):
            self.assertEqual(len(prepared), 3)
            return original_mkdir(path, *args, **kwargs)
        with patch.object(panel, "build_prompt", side_effect=build), patch.object(Path, "mkdir", mkdir):
            code, _, _ = self.invoke("--prompts-only", "--groups", "10,4,5")
        self.assertEqual(code, 0)

    def test_input_write_failure_never_starts_workers(self):
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path.parent.name == "inputs":
                raise PermissionError("synthetic deny")
            return original(path, *args, **kwargs)
        with patch.object(Path, "write_text", write), patch.object(panel, "run_one") as worker:
            code, _, error = self.invoke("--engine", "claude", "--groups", "10")
        self.assertNotEqual(code, 0)
        self.assertIn("尚未启动审稿进程", error)
        worker.assert_not_called()

    def test_intent_attribute_alignment_preserves_diagnostic_rules(self):
        for audience, medium in [("同行（未提供知识范围）", "八分钟旁白"), ("未知受众", "图文长文")]:
            prompt = panel.intent_prompt("10-用意", "Only draft.", audience, medium)
            self.assertIn(audience, prompt)
            self.assertIn(medium, prompt)
            self.assertNotIn("这条视频", prompt)
            self.assertNotIn("只听一遍", prompt)
            self.assertNotIn("普通人", prompt)
            self.assertNotIn("两分钟", prompt)
            self.assertIn("口播按首次听到，图文按首次阅读", prompt)
            self.assertIn("未说明的知识不自行假定", prompt)
            self.assertIn("同行身份不等于懂所有知识或看过原材料", prompt)
            for criterion in ["能", "暂留待兑现", "说不全", "说不出", "必须修", "可选增强", "未解决", "1000 字以内"]:
                self.assertIn(criterion, prompt)


class PanelEvidenceTests(OfflineCase):
    def test_partial_failure_retains_evidence(self):
        output = self.task / "answer.md"
        def respond(*args, **kwargs):
            output.write_text("partial feedback")
            return subprocess.CompletedProcess([], 1, "", "synthetic failure")
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(panel.subprocess, "run", side_effect=respond):
            result = panel.run_one("codex", self.task, "test", "prompt", output, 1, "xhigh", None)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["output"])
        self.assertEqual(result["execution"]["call_stage"], "exited")
        self.assertTrue(result["execution"]["nonempty_feedback_received"])
        self.assertEqual(result["execution"]["feedback_artifact"], str(output))
        self.assertEqual(result["execution"]["draft_quality"], "not_assessed")
        self.assertEqual(result["execution"]["author_adoption"], "unknown")
        self.assertEqual(result["execution"]["post_revision_check"], "unknown")

    def test_timeout_partial_feedback_not_lost(self):
        output = self.task / "answer.md"
        def timeout(*args, **kwargs):
            output.write_text("partial")
            raise subprocess.TimeoutExpired("mock", 1)
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(panel.subprocess, "run", side_effect=timeout):
            result = panel.run_one("codex", self.task, "test", "prompt", output, 1, "xhigh", None)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["execution"]["nonempty_feedback_received"])

    def test_spawn_failure_does_not_claim_request_started(self):
        with patch.object(panel, "command", return_value=(["mock"], False)), patch.object(panel.subprocess, "run", side_effect=PermissionError("no start")):
            result = panel.run_one("codex", self.task, "test", "prompt", self.task / "answer.md", 1, "xhigh", None)
        self.assertEqual(result["execution"]["call_stage"], "attempted")


    def test_claude_nonzero_and_timeout_stdout_saved_as_partial_not_success(self):
        for kind in ["nonzero", "timeout"]:
            output = self.task / (kind + ".md")
            outcome = subprocess.CompletedProcess([], 1, "partial stdout", "")
            effect = subprocess.TimeoutExpired("mock", 1, output=b"partial timeout") if kind == "timeout" else None
            with patch.object(panel, "command", return_value=(["mock"], True)), patch.object(panel.subprocess, "run", return_value=outcome, side_effect=effect):
                result = panel.run_one("claude", self.task, "test", "prompt", output, 1, "xhigh", None)
            self.assertIn(result["status"], ["failed", "timeout"])
            self.assertIsNone(result["output"])
            self.assertTrue(result["execution"]["nonempty_feedback_received"])
            self.assertEqual(result["execution"]["partial_artifact"], str(output))
            self.assertEqual(result["execution"]["external_requests"], "not_observed")

    def test_completed_status_is_execution_only(self):
        with patch.object(panel, "command", return_value=(["mock"], True)), patch.object(panel.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Feedback.", "")):
            code, summary, _ = self.invoke("--engine", "claude", "--groups", "10")
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["review_state"], "reviews_received")
        self.assertEqual(summary["execution"]["reviews_received"], 1)
        self.assertEqual(summary["execution"]["draft_quality"], "not_assessed")
        self.assertEqual(summary["execution"]["author_adoption"], "unknown")

    def test_partial_reviews_summary_does_not_count_failed_artifacts(self):
        def respond(*args, **kwargs):
            failed = "10-用意" in kwargs["input"]
            return subprocess.CompletedProcess([], 1 if failed else 0, "Feedback.", "")
        with patch.object(panel, "command", return_value=(["mock"], True)), patch.object(panel.subprocess, "run", side_effect=respond):
            code, summary, _ = self.invoke("--engine", "claude", "--groups", "4,10")
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["review_state"], "partial_reviews_received")
        self.assertEqual(summary["execution"]["reviews_received"], 1)
        self.assertEqual(summary["execution"]["partial_artifacts"], 1)

    def test_late_summary_write_failure_retains_stdout_results(self):
        save = panel.save_summary
        calls = []
        def flaky(path, summary):
            calls.append(1)
            if len(calls) > 1:
                raise PermissionError("synthetic deny")
            save(path, summary)
        with patch.object(panel, "save_summary", side_effect=flaky), patch.object(panel, "command", return_value=(["mock"], True)), patch.object(panel.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Feedback.", "")):
            code, summary, _ = self.invoke("--engine", "claude", "--groups", "10")
        self.assertEqual(code, 1)
        self.assertEqual(summary["results"][0]["execution"]["call_stage"], "exited")
        self.assertEqual(summary["review_state"], "reviews_received")
        self.assertIn("persistence_error", summary)


class FirstReaderReliabilityTests(OfflineCase):
    def test_payload_preserves_explicit_audience_and_duration_without_source_focus(self):
        payload = reader.build_payload("Synthetic draft.", "同行编辑", "八分钟图文", "synthetic-key")
        user = json.loads(payload["messages"][1]["content"])
        self.assertEqual(user, {"读者画像": "同行编辑", "呈现方式": "八分钟图文", "稿子": "Synthetic draft."})
        self.assertNotIn("SOURCE_SENTINEL", json.dumps(payload))
        self.post_mock.assert_not_called()
        self.key_mock.assert_not_called()

    def test_budget_repository_path_rejected(self):
        with self.assertRaises(reader.Failure):
            reader.budget_path(self.repo / "budget.json", self.task)

    def test_budget_symlink_rejected(self):
        (self.task / "budget.json").symlink_to(self.repo / "ledger.json")
        with self.assertRaises(reader.Failure):
            reader.budget_path(self.task / "budget.json", self.task)

    def test_budget_mismatch_and_symlink_loop_fail_cleanly(self):
        loop = self.task / "loop"
        loop.symlink_to(loop)
        with patch.dict(reader.os.environ, {"STORY_CRAFT_READER_BUDGET": str(loop)}):
            with self.assertRaises(reader.Failure) as caught:
                reader.budget_path(self.base / "ledger.json", self.task)
            self.assertEqual(caught.exception.code, "budget_unsafe")
        with patch.dict(reader.os.environ, {"STORY_CRAFT_READER_BUDGET": str(self.base / "one.json")}):
            with self.assertRaises(reader.Failure) as caught:
                reader.budget_path(self.base / "two.json", self.task)
            self.assertEqual(caught.exception.code, "budget_mismatch")

    def test_budget_external_real_file_allowed(self):
        path = self.base / "shared-budget.json"
        self.assertEqual(reader.budget_path(path, self.task), path)

    def test_reader_gate_preserved(self):
        with self.assertRaises(reader.Failure) as caught:
            reader.run(self.task, ["draft.md"])
        self.assertEqual(caught.exception.code, "external_not_authorized")
        self.key_mock.assert_not_called()
        self.post_mock.assert_not_called()


    def test_budget_parent_symlink_and_directory_target_rejected(self):
        link = self.task / "linked"
        link.symlink_to(self.base, target_is_directory=True)
        for path in [link / "ledger.json", self.task]:
            with self.subTest(path=path), self.assertRaises(reader.Failure):
                reader.budget_path(path, self.task)

    def test_budget_main_repository_from_worktree_rejected(self):
        worktree = self.repo / "worktree"
        worktree.mkdir()
        admin = self.repo / ".git/worktrees/reliability"
        admin.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: " + str(admin))
        (admin / "commondir").write_text("../..")
        with patch.object(reader, "ROOT", worktree):
            for path in [self.repo / "budget.json", admin / "budget.json"]:
                with self.subTest(path=path), self.assertRaises(reader.Failure):
                    reader.budget_path(path, self.task)

    def run_reader(self, **kwargs):
        return reader.run(self.task, ["draft.md"], allow_external=True, **kwargs)

    @staticmethod
    def response(finish="stop", content="Synthetic feedback.", status=200):
        def respond(payload, key, timeout, trace):
            trace["http_status"] = status
            trace["raw"].extend(json.dumps({"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                           "usage": {"total_tokens": 12}}).encode())
        return respond

    def test_reader_explicit_context_in_payload_receipt_and_result(self):
        audience, medium = " 同行编辑 ", " 八分钟旁白 "
        with patch.object(reader, "post_once", side_effect=self.response()) as post:
            result = self.run_reader(audience=audience, medium=medium)
        self.assertTrue(result["independent_return"])
        self.assertEqual(post.call_count, 1)
        sent = json.loads(post.call_args.args[0]["messages"][1]["content"])
        self.assertEqual(sent["读者画像"], audience)
        self.assertEqual(sent["呈现方式"], medium)
        self.assertEqual(set(sent), {"读者画像", "呈现方式", "稿子"})
        receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
        for context in [result["task_context"], receipt["task_context"]]:
            self.assertEqual(context["audience"], {"value": audience, "source": "explicit"})
            self.assertEqual(context["medium"], {"value": medium, "source": "explicit"})
        self.assertEqual(receipt["execution"]["draft_quality"], "not_assessed")
        self.assertEqual(receipt["execution"]["author_adoption"], "unknown")
        self.assertEqual(receipt["review_state"], "reviews_received")

    def test_reader_omission_remains_callable_but_context_is_unconfirmed(self):
        with patch.object(reader, "post_once", side_effect=self.response()):
            result = self.run_reader()
        self.assertEqual(result["task_context"]["medium"], {"value": "未提供（形式与时长未确认）", "source": "default_assumption"})
        self.assertEqual(result["task_context"]["audience"]["source"], "default_assumption")
        self.assertEqual(reader.DEFAULT_LIMIT, 6)
        self.assertEqual(reader.MAX_CALLS, 20)

    def test_reader_omitted_payload_marks_context_unconfirmed(self):
        with patch.object(reader, "post_once", side_effect=self.response()) as post:
            self.run_reader()
        sent = json.loads(post.call_args.args[0]["messages"][1]["content"])
        self.assertIn("未确认", sent["读者画像"])
        self.assertIn("未确认", sent["呈现方式"])
        self.assertNotIn("两分钟", sent["呈现方式"])
        self.assertNotIn("普通观众", sent["读者画像"])

    def test_budget_cannot_overwrite_input_draft(self):
        draft = self.task / "draft.md"
        original = json.dumps({"authorized": True, "limit": 6, "attempts": []})
        draft.write_text(original)
        caught = None
        with patch.object(reader, "post_once", side_effect=self.response()) as post:
            try:
                self.run_reader(budget_file=draft)
            except reader.Failure as exc:
                caught = exc
        self.assertEqual(draft.read_text(), original, "budget wrote over the input draft")
        self.assertIsNotNone(caught)
        self.assertEqual(caught.code, "budget_input_conflict")
        self.assertFalse((self.task / "first-reader").exists())
        post.assert_not_called()

    def test_budget_input_hardlink_conflict_rejected(self):
        draft = self.task / "draft.md"
        original = json.dumps({"authorized": True, "limit": 6, "attempts": []})
        draft.write_text(original)
        budget = self.base / "shared-budget.json"
        budget.hardlink_to(draft)
        with self.assertRaises(reader.Failure) as caught:
            self.run_reader(budget_file=budget)
        self.assertEqual(caught.exception.code, "budget_input_conflict")
        self.assertEqual(draft.read_text(), original)
        self.assertFalse((self.task / "first-reader").exists())
        self.post_mock.assert_not_called()

    def test_reader_empty_attributes_fail_before_config_or_outputs(self):
        for kwargs in [{"audience": ""}, {"medium": "  "}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(reader.Failure) as caught:
                self.run_reader(**kwargs)
            self.assertEqual(caught.exception.code, "invalid_arguments")
        self.key_mock.assert_not_called()
        self.post_mock.assert_not_called()
        self.assertFalse((self.task / "first-reader").exists())

    def test_reader_partial_response_retained_without_claiming_no_attempt(self):
        with patch.object(reader, "post_once", side_effect=self.response(finish="length")):
            result = self.run_reader(audience="同行", medium="八分钟旁白")
        item = result["results"][0]
        self.assertFalse(item["independent_return"])
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["review_state"], "no_reviews_received")
        self.assertTrue(item["execution"]["nonempty_feedback_received"])
        self.assertGreater(item["execution"]["response_bytes_received"], 0)
        self.assertEqual(item["execution"]["call_stage"], "returned")
        self.assertIn("已尝试", item["message"])
        self.assertNotIn("没做独立审查", item["message"])
        self.assertIsNone(item["feedback"])
        self.assertTrue(Path(item["receipt"]).with_name("response.json").is_file())

    def test_reader_unsafe_budget_preflight_has_no_output_or_provider(self):
        budget = self.repo / "budget.json"
        original = json.dumps({"authorized": True, "limit": 6, "attempts": []})
        budget.write_text(original)
        with self.assertRaises(reader.Failure) as caught:
            self.run_reader(budget_file=budget)
        self.assertEqual(caught.exception.code, "budget_unsafe")
        self.assertEqual(budget.read_text(), original)
        self.assertFalse((self.task / "first-reader").exists())
        self.post_mock.assert_not_called()

    def test_reader_external_temporary_directory_without_path_mock(self):
        with patch.object(reader, "post_once", side_effect=self.response()) as post:
            result = reader.run(self.task, ["draft.md"], allow_external=True)
        self.assertTrue(result["independent_return"])
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(Path(result["results"][0]["receipt"]).is_relative_to(self.task))

    def test_reader_draft_copy_failure_is_processed_but_not_sent(self):
        write = Path.write_text
        def fail_draft_copy(path, *args, **kwargs):
            if path.name == "draft-reviewed.md":
                raise PermissionError("synthetic draft copy denied")
            return write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail_draft_copy):
            result = self.run_reader()
        self.post_mock.assert_not_called()
        item = result["results"][0]
        receipt = json.loads(Path(item["receipt"]).read_text())
        self.assertEqual(item["status"], "failed")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error_code"], "local_error")
        self.assertEqual(receipt["execution"]["call_stage"], "not_started")
        self.assertFalse(receipt["independent_return"])
        self.assertEqual(receipt["execution"]["response_bytes_received"], 0)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent_semantics"], "client_request_attempts_not_provider_receipts_or_billing")

    def test_proven_not_started_failure_releases_one_budget_unit(self):
        budget = self.base / "shared-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        write = Path.write_text
        def fail_draft_copy(path, *args, **kwargs):
            if path.name == "draft-reviewed.md":
                raise PermissionError("synthetic draft copy denied")
            return write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail_draft_copy):
            first = self.run_reader(budget_file=budget)
        self.post_mock.assert_not_called()
        receipt_path = Path(first["results"][0]["receipt"])
        saved_failure = receipt_path.read_bytes()
        receipt = json.loads(saved_failure)
        self.assertEqual(receipt["execution"]["call_stage"], "not_started")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(first["sent"], 0)
        after_failure = reader.read_budget(budget)
        available_error = None
        try:
            reader.available(after_failure, 1)
        except reader.Failure as exc:
            available_error = exc.code
        retry_error = None
        with patch.object(reader, "post_once", side_effect=self.response()) as post:
            try:
                second = self.run_reader(budget_file=budget)
            except reader.Failure as exc:
                retry_error = exc.code
            else:
                with self.assertRaises(reader.Failure) as caught:
                    self.run_reader(budget_file=budget)
                self.assertEqual(caught.exception.code, "budget_exhausted")
                self.assertEqual(post.call_count, 1)
                self.assertEqual(receipt_path.read_bytes(), saved_failure)
        self.assertIsNone(retry_error, "sent=0, post_once never called on failed copy; receipt.attempts=%s; "
                          "available(1)=%s; ledger=%s" % (receipt["attempts"], available_error, after_failure))
        self.assertIsNone(available_error)
        self.assertEqual(receipt["attempts"], 0)
        self.assertEqual(receipt["attempts_semantics"], first["sent_semantics"])
        self.assertEqual(after_failure["limit"] - reader.budget_used(after_failure), 1)
        self.assertTrue(second["independent_return"])
        self.assertEqual(second["sent"], 1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(receipt_path.read_bytes(), saved_failure)
        final = reader.read_budget(budget)
        self.assertEqual(final["limit"], 1)
        self.assertEqual(len(final["attempts"]), 2)
        self.assertEqual(final["attempts"][0]["status"], "local_error")
        self.assertTrue(final["attempts"][0]["quota_released"])
        self.assertEqual(final["attempts"][0]["client_request_attempts"], 0)
        self.assertEqual(final["attempts"][1]["status"], "completed")
        self.assertEqual(final["attempts"][1]["client_request_attempts"], 1)
        self.assertEqual(final["limit"] - reader.budget_used(final), 0)
        with self.assertRaises(reader.Failure) as caught:
            reader.available(final, 1)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_request_file_failure_also_releases_only_after_preserving_receipt(self):
        budget = self.base / "request-failure-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        write = Path.write_text
        def fail_request(path, *args, **kwargs):
            if path.name == "request.json":
                raise PermissionError("synthetic request write denied")
            return write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail_request):
            result = self.run_reader(budget_file=budget)
        self.post_mock.assert_not_called()
        receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
        data = reader.read_budget(budget)
        self.assertEqual(receipt["attempts"], 0)
        self.assertEqual(receipt["execution"]["call_stage"], "not_started")
        self.assertEqual(data["attempts"][0]["status"], "local_error")
        self.assertTrue(data["attempts"][0]["quota_released"])
        self.assertEqual(reader.budget_used(data), 0)
        reader.available(data, 1)
        self.assertTrue(Path(result["results"][0]["receipt"]).with_name("draft-reviewed.md").is_file())

    def test_historical_reserved_unknown_or_missing_stage_never_refunded(self):
        historical = [
            {"id": "old-reserved", "status": "reserved"},
            {"id": "old-requesting", "status": "requesting"},
            {"id": "old-local-error", "status": "local_error"},
            {"id": "old-unstarted", "status": "local_error", "call_stage": "not_started", "client_request_attempts": 0},
            {"id": "old-unknown", "status": "local_error", "call_stage": "unknown", "client_request_attempts": None},
            {"id": "ambiguous-release", "status": "local_error", "call_stage": "unknown", "client_request_attempts": 0, "quota_released": True},
        ]
        for entry in historical:
            with self.subTest(entry=entry):
                budget = self.base / (entry["id"] + ".json")
                budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": [entry]}))
                original = budget.read_bytes()
                self.assertEqual(reader.budget_used(reader.read_budget(budget)), 1)
                with self.assertRaises(reader.Failure) as caught:
                    self.run_reader(budget_file=budget)
                self.assertEqual(caught.exception.code, "budget_exhausted")
                self.assertEqual(budget.read_bytes(), original)
        self.post_mock.assert_not_called()

    def test_requesting_persistence_failures_are_unknown_and_remain_charged(self):
        for after_write in [False, True]:
            with self.subTest(after_write=after_write):
                budget = self.base / ("transition-after.json" if after_write else "transition-before.json")
                budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
                write_json = reader.write_json
                failed = []
                def fail_transition(path, value):
                    if path == budget and any(x["status"] == "requesting" for x in value["attempts"]) and not failed:
                        if after_write:
                            write_json(path, value)
                        failed.append(json.loads(budget.read_text())["attempts"][0]["status"])
                        raise OSError("synthetic ambiguous requesting persistence")
                    return write_json(path, value)
                with patch.object(reader, "write_json", side_effect=fail_transition):
                    result = self.run_reader(budget_file=budget)
                self.assertEqual(failed, ["requesting" if after_write else "reserved"])
                self.post_mock.assert_not_called()
                receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
                data = reader.read_budget(budget)
                self.assertEqual(receipt["execution"]["call_stage"], "unknown")
                self.assertIsNone(receipt["attempts"])
                self.assertEqual(result["sent_unknown"], 1)
                self.assertIn("是否发起未确认", result["results"][0]["message"])
                self.assertFalse(data["attempts"][0]["quota_released"])
                self.assertIsNone(data["attempts"][0]["client_request_attempts"])
                self.assertEqual(reader.budget_used(data), 1)
                saved = budget.read_bytes()
                with self.assertRaises(reader.Failure) as caught:
                    self.run_reader(budget_file=budget)
                self.assertEqual(caught.exception.code, "budget_exhausted")
                self.assertEqual(budget.read_bytes(), saved)

    def test_failed_terminal_evidence_does_not_release_active_reservation(self):
        for failed_name in ["receipt.json", "usage.json"]:
            with self.subTest(failed_name=failed_name):
                budget = self.base / (failed_name + ".budget.json")
                budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
                write = Path.write_text
                receipt_writes = []
                def fail_evidence(path, *args, **kwargs):
                    if path.name == "receipt.json":
                        receipt_writes.append(1)
                    if path.name == "draft-reviewed.md" or (path.name == failed_name and
                            (failed_name != "receipt.json" or len(receipt_writes) == 2)):
                        raise PermissionError("synthetic final evidence unavailable")
                    return write(path, *args, **kwargs)
                with patch.object(Path, "write_text", fail_evidence), self.assertRaises(OSError):
                    self.run_reader(budget_file=budget)
                self.post_mock.assert_not_called()
                data = reader.read_budget(budget)
                self.assertEqual(len(data["attempts"]), 1)
                self.assertEqual(data["attempts"][0]["status"], "reserved")
                self.assertNotIn("quota_released", data["attempts"][0])
                self.assertEqual(reader.budget_used(data), 1)
                with self.assertRaises(reader.Failure) as caught:
                    reader.available(data, 1)
                self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_interrupted_active_worker_is_not_cancelled_as_unprocessed(self):
        budget = self.base / "interrupted-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        with patch.object(reader, "run_one", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_reader(budget_file=budget)
        data = reader.read_budget(budget)
        self.assertEqual(len(data["attempts"]), 1)
        self.assertEqual(data["attempts"][0]["status"], "reserved")
        self.assertEqual(reader.budget_used(data), 1)
        self.post_mock.assert_not_called()

    def test_attempted_errors_timeout_and_feedback_write_failure_keep_charge(self):
        for kind in ["post_oserror", "timeout", "transport_error", "feedback_write"]:
            with self.subTest(kind=kind):
                budget = self.base / (kind + ".json")
                budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
                respond = self.response()
                def post(payload, key, timeout, trace):
                    if kind == "post_oserror":
                        raise OSError("synthetic post error")
                    if kind == "timeout":
                        trace["timed_out"] = True
                    elif kind == "transport_error":
                        trace["transport_error"] = True
                    else:
                        respond(payload, key, timeout, trace)
                write = Path.write_text
                def write_feedback(path, *args, **kwargs):
                    if kind == "feedback_write" and path.name == "feedback.md":
                        raise PermissionError("synthetic feedback write denied")
                    return write(path, *args, **kwargs)
                with patch.object(reader, "post_once", side_effect=post) as called, patch.object(Path, "write_text", write_feedback):
                    result = self.run_reader(budget_file=budget)
                    self.assertEqual(called.call_count, 1)
                    with self.assertRaises(reader.Failure) as caught:
                        self.run_reader(budget_file=budget)
                    self.assertEqual(caught.exception.code, "budget_exhausted")
                    self.assertEqual(called.call_count, 1)
                receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
                data = reader.read_budget(budget)
                self.assertEqual(result["sent"], 1)
                self.assertEqual(result["sent_unknown"], 0)
                self.assertEqual(receipt["attempts"], 1)
                self.assertEqual(receipt["status"], "failed")
                self.assertFalse(data["attempts"][0]["quota_released"])
                self.assertEqual(data["attempts"][0]["client_request_attempts"], 1)
                self.assertEqual(reader.budget_used(data), 1)

    def test_stale_initial_receipt_does_not_refund_after_attempted_finalization_failure(self):
        budget = self.base / "finalization-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        write = Path.write_text
        def fail_usage(path, *args, **kwargs):
            if path.name == "usage.json":
                raise PermissionError("synthetic usage save denied")
            return write(path, *args, **kwargs)
        with patch.object(reader, "post_once", side_effect=self.response()) as post, patch.object(Path, "write_text", fail_usage):
            with self.assertRaises(OSError):
                self.run_reader(budget_file=budget)
        self.assertEqual(post.call_count, 1)
        data = reader.read_budget(budget)
        self.assertEqual(data["attempts"][0]["status"], "requesting")
        self.assertNotIn("quota_released", data["attempts"][0])
        initial = json.loads((Path(data["attempts"][0]["output_dir"]) / "receipt.json").read_text())
        self.assertEqual(initial["status"], "requesting")
        self.assertEqual(initial["attempts"], 0)  # Initial snapshot, not terminal proof.
        self.assertEqual(reader.budget_used(data), 1)
        with self.assertRaises(reader.Failure) as caught:
            self.run_reader(budget_file=budget)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_requesting_unlock_error_keeps_unknown_attempt_charged(self):
        budget = self.base / "unlock-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        ledger = reader.ledger
        failed = []
        @contextlib.contextmanager
        def fail_exit(path):
            with ledger(path) as data:
                yield data
                fail = any(x["status"] == "requesting" for x in data["attempts"]) and not failed
            if fail:
                failed.append(True)
                raise OSError("synthetic unlock failure after requesting write")
        with patch.object(reader, "ledger", fail_exit):
            result = self.run_reader(budget_file=budget)
        self.post_mock.assert_not_called()
        receipt = json.loads(Path(result["results"][0]["receipt"]).read_text())
        self.assertEqual(failed, [True])
        self.assertEqual(receipt["execution"]["call_stage"], "unknown")
        self.assertIsNone(receipt["attempts"])
        self.assertEqual(result["sent_unknown"], 1)
        data = reader.read_budget(budget)
        self.assertEqual(reader.budget_used(data), 1)
        self.assertFalse(data["attempts"][0]["quota_released"])
        with self.assertRaises(reader.Failure) as caught:
            self.run_reader(budget_file=budget)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_only_current_unprocessed_reservations_cancel_after_provider_stop(self):
        budget = self.base / "stop-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 2, "attempts": []}))
        (self.task / "other.md").write_text("Other synthetic draft.")
        with patch.object(reader, "post_once", side_effect=self.response(status=402)) as post:
            result = reader.run(self.task, ["draft.md", "other.md"], budget_file=budget, allow_external=True)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent"], 1)
        data = reader.read_budget(budget)
        self.assertEqual(data["limit"], 2)
        self.assertEqual(len(data["attempts"]), 1)
        self.assertEqual(data["attempts"][0]["status"], "payment_required")
        self.assertEqual(reader.budget_used(data), 1)
        self.assertTrue(data["stop_reason"])

    def test_settle_write_failure_keeps_active_reservation_charged(self):
        budget = self.base / "settle-failure-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 1, "attempts": []}))
        write, write_json = Path.write_text, reader.write_json
        failed = []
        def fail_draft(path, *args, **kwargs):
            if path.name == "draft-reviewed.md":
                raise PermissionError("synthetic draft copy denied")
            return write(path, *args, **kwargs)
        def fail_settle(path, value):
            if path == budget and any(x.get("quota_released") is True for x in value["attempts"]):
                failed.append(True)
                raise OSError("synthetic quota settlement write denied")
            return write_json(path, value)
        with patch.object(Path, "write_text", fail_draft), patch.object(reader, "write_json", side_effect=fail_settle):
            with self.assertRaises(OSError):
                self.run_reader(budget_file=budget)
        self.assertEqual(failed, [True])
        self.post_mock.assert_not_called()
        data = reader.read_budget(budget)
        self.assertEqual(len(data["attempts"]), 1)
        self.assertEqual(data["attempts"][0]["status"], "reserved")
        self.assertEqual(reader.budget_used(data), 1)
        receipt = json.loads((Path(data["attempts"][0]["output_dir"]) / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["attempts"], 0)
        with self.assertRaises(reader.Failure) as caught:
            self.run_reader(budget_file=budget)
        self.assertEqual(caught.exception.code, "budget_exhausted")

    def test_reader_external_shared_budget_retained(self):
        budget = self.base / "shared-budget.json"
        budget.write_text(json.dumps({"authorized": True, "limit": 6, "attempts": []}))
        with patch.object(reader, "post_once", side_effect=self.response()):
            result = self.run_reader(budget_file=budget)
        self.assertTrue(result["independent_return"])
        data = json.loads(budget.read_text())
        self.assertEqual(data["limit"], 6)
        self.assertEqual(len(data["attempts"]), 1)
        self.assertEqual(data["attempts"][0]["status"], "completed")


if __name__ == "__main__":
    # This entry point also protects the unchanged legacy tests from accidental IO.
    with contextlib.ExitStack() as guards:
        for target in ["socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
                       "socket.getaddrinfo", "subprocess.Popen"]:
            guards.enter_context(patch(target, side_effect=AssertionError("OFFLINE: " + target)))
        guards.enter_context(patch.object(panel, "codex_model", side_effect=AssertionError("NO USER CONFIG")))
        guards.enter_context(patch.object(reader, "read_key", side_effect=AssertionError("NO CREDENTIALS")))
        guards.enter_context(patch.object(reader, "post_once", side_effect=AssertionError("NO PROVIDERS")))
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
