#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "autoreview"


def load_helper() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT), run_name="autoreview_under_test")


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Autoreview Test",
            "GIT_AUTHOR_EMAIL": "autoreview@example.invalid",
            "GIT_COMMITTER_NAME": "Autoreview Test",
            "GIT_COMMITTER_EMAIL": "autoreview@example.invalid",
        }
    )
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def init_repo(tempdir: Path) -> Path:
    repo = tempdir / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Autoreview Test")
    git(repo, "config", "user.email", "autoreview@example.invalid")
    return repo


class AutoreviewHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def test_powershell_harness_exposes_runnable_engines_only(self) -> None:
        harness = SCRIPT.with_name("test-review-harness.ps1").read_text(encoding="utf-8")

        self.assertIn("[ValidateSet('codex', 'claude', 'pi')]", harness)
        for disabled_engine in ("droid", "copilot", "opencode", "cursor"):
            self.assertNotIn(f"'{disabled_engine}'", harness)

    def test_local_bundle_marks_untracked_binary_input_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "image.bin").write_bytes(b"\x89PNG\r\n\0binary-content")

            bundle, truncated = self.helper["local_bundle"](repo)

            self.assertIn(
                '# Untracked File\npath: "image.bin"\n'
                'source-line 1: "[binary file omitted]"',
                bundle,
            )
            self.assertTrue(truncated)

    def test_local_bundle_rejects_non_utf8_untracked_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "latin.py").write_bytes(b"print('caf\xe9')\n")

            with self.assertRaisesRegex(SystemExit, "non-UTF-8 file"):
                self.helper["local_bundle"](repo)

    def test_local_bundle_uses_validated_untracked_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "notes.txt").write_text("review me\n", encoding="utf-8")
            original_read_prefix = self.helper["read_prefix"]
            reads = 0

            def read_once(path: Path, limit: int) -> tuple[bytes, bool]:
                nonlocal reads
                reads += 1
                if reads > 1:
                    raise AssertionError("untracked file was reopened after validation")
                return original_read_prefix(path, limit)

            with mock.patch.dict(
                self.helper["local_bundle"].__globals__,
                {"read_prefix": read_once},
            ):
                bundle, truncated = self.helper["local_bundle"](repo)

            expected_record = json.dumps("review me" + os.linesep)
            self.assertIn(
                '# Untracked File\npath: "notes.txt"\n'
                f"source-line 1: {expected_record}",
                bundle,
            )
            self.assertFalse(truncated)
            self.assertEqual(reads, 1)

    def test_tracked_binary_changes_are_blocked_in_all_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            binary = repo / "artifact.bin"
            binary.write_bytes(b"\0base")
            git(repo, "add", "artifact.bin")
            git(repo, "commit", "-q", "-m", "base")
            base = git(repo, "rev-parse", "HEAD").strip()

            binary.write_bytes(b"\0changed")
            git(repo, "add", "artifact.bin")
            with self.assertRaisesRegex(SystemExit, "refusing binary changes"):
                self.helper["local_bundle"](repo)

            git(repo, "commit", "-q", "-m", "binary change")
            with self.assertRaisesRegex(SystemExit, "refusing binary changes"):
                self.helper["commit_bundle"](repo, "HEAD")
            with self.assertRaisesRegex(SystemExit, "refusing binary changes"):
                self.helper["branch_bundle"](repo, base)

    def test_gitlink_changes_are_blocked_in_all_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            tracked = repo / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-q", "-m", "base")
            base = git(repo, "rev-parse", "HEAD").strip()

            git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{base},vendor/dependency",
            )
            with self.assertRaisesRegex(SystemExit, "gitlink/submodule changes"):
                self.helper["local_bundle"](repo)

            git(repo, "commit", "-q", "-m", "add gitlink")
            with self.assertRaisesRegex(SystemExit, "gitlink/submodule changes"):
                self.helper["commit_bundle"](repo, "HEAD")
            with self.assertRaisesRegex(SystemExit, "gitlink/submodule changes"):
                self.helper["branch_bundle"](repo, base)

    def test_gitlink_guard_parses_combined_raw_modes(self) -> None:
        raw_diff = (
            "::100644 100644 160000 "
            + ("a" * 40)
            + " "
            + ("b" * 40)
            + " "
            + ("c" * 40)
            + " MM\0vendor/dependency\0"
        )

        with self.assertRaisesRegex(SystemExit, "gitlink/submodule changes"):
            self.helper["require_no_gitlink_diff"]("merge diff", raw_diff)

    def test_codex_config_rejects_capability_bearing_overrides(self) -> None:
        for override in (
            'mcp_servers.review.command="touch /tmp/owned"',
            'notify=["sh", "-c", "touch /tmp/owned"]',
            'model_instructions_file="/tmp/hostile.md"',
            'model_provider="credential-sink"',
            'hooks.PreToolUse.command="touch /tmp/owned"',
        ):
            with self.subTest(override=override), self.assertRaisesRegex(
                SystemExit,
                "unsafe Codex config override refused",
            ):
                self.helper["codex_config_overrides"](
                    argparse.Namespace(codex_config=[override])
                )

    def test_codex_config_accepts_safe_tuning_overrides(self) -> None:
        args = argparse.Namespace(
            codex_config=[
                'service_tier="fast"',
                'model_verbosity="low"',
                'model_reasoning_summary="concise"',
            ]
        )

        self.assertEqual(
            self.helper["codex_config_overrides"](args),
            args.codex_config,
        )

    def test_untracked_files_respect_trusted_global_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            home = root / "home"
            home.mkdir()
            excludes = root / "global-ignore"
            excludes.write_text(
                "ignored.local\n!settings.local\n",
                encoding="utf-8",
            )
            (home / ".gitconfig").write_text(
                f"[core]\n\texcludesFile = {excludes.as_posix()}\n",
                encoding="utf-8",
            )
            (repo / "ignored.local").write_text("private notes\n", encoding="utf-8")
            (repo / ".gitignore").write_text("settings.local\n", encoding="utf-8")
            (repo / "settings.local").write_text("repo private\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            (repo / "visible.txt").write_text("review me\n", encoding="utf-8")
            (repo / "hostile-gitconfig").write_text(
                "[core]\n\texcludesFile = /does/not/exist\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "GIT_CONFIG_GLOBAL": str(repo / "hostile-gitconfig"),
                },
            ):
                self.assertEqual(
                    self.helper["safe_untracked_files"](repo),
                    ["hostile-gitconfig", "visible.txt"],
                )

    def test_dirty_check_respects_trusted_global_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            home = root / "home"
            home.mkdir()
            excludes = root / "global-ignore"
            excludes.write_text("ignored.local\n", encoding="utf-8")
            (home / ".gitconfig").write_text(
                f"[core]\n\texcludesFile = {excludes.as_posix()}\n",
                encoding="utf-8",
            )
            (repo / "ignored.local").write_text("private notes\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                },
            ):
                self.assertFalse(self.helper["is_dirty"](repo))

    def test_oversized_text_is_rejected_without_scanning_binary_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            tail_marker = "\ntoken=" + "A" * 24 + "\n"
            content = "x" * (64_000 * 3 - 4) + tail_marker

            untracked = repo / "untracked.txt"
            untracked.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "file too large to scan safely"):
                self.helper["safe_untracked_files"](repo)

            untracked.unlink()
            binary = repo / "binary.bin"
            binary.write_bytes(b"\0" + content.encode())
            self.assertEqual(
                self.helper["safe_untracked_files"](repo),
                ["binary.bin"],
            )

            binary.unlink()
            evidence = repo / "evidence.txt"
            evidence.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "file too large to scan safely"):
                self.helper["validate_evidence_file"](repo, "evidence.txt", "--dataset")

    def test_branch_bundle_rejects_unsafe_or_unknown_base_before_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-q", "-m", "base")

            with self.assertRaisesRegex(SystemExit, "unsafe base ref"):
                self.helper["branch_bundle"](repo, "--help")
            with self.assertRaisesRegex(SystemExit, "unknown base ref"):
                self.helper["branch_bundle"](repo, "origin/main")

    def test_commit_bundle_rejects_merge_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git(repo, "add", "base.txt")
            git(repo, "commit", "-q", "-m", "base")
            base_branch = git(repo, "branch", "--show-current").strip()
            git(repo, "checkout", "-q", "-b", "side")
            (repo / "side.txt").write_text("side\n", encoding="utf-8")
            git(repo, "add", "side.txt")
            git(repo, "commit", "-q", "-m", "side")
            git(repo, "checkout", "-q", base_branch)
            (repo / "main.txt").write_text("main\n", encoding="utf-8")
            git(repo, "add", "main.txt")
            git(repo, "commit", "-q", "-m", "main")
            git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")

            with self.assertRaisesRegex(SystemExit, "does not accept merge commits"):
                self.helper["commit_bundle"](repo, "HEAD")

    def test_git_path_list_preserves_newline_filenames(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows filesystems do not support newline path components")
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            rel = "line\nbreak.txt"
            (repo / rel).write_text("content\n", encoding="utf-8")
            git(repo, "add", rel)

            paths = self.helper["git_path_list"](repo, "ls-files", "-z")

            self.assertIn(rel, paths)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires raw non-UTF-8 filename support")
    def test_git_path_list_rejects_non_utf8_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            rel = os.fsdecode(b"invalid-\xff.txt")
            (repo / rel).write_text("content\n", encoding="utf-8")
            git(repo, "add", "--", rel)

            with self.assertRaisesRegex(SystemExit, "non-UTF-8 Git output"):
                self.helper["git_path_list"](repo, "ls-files", "-z")

    def test_review_patch_rejects_oversized_content(self) -> None:
        with self.assertRaisesRegex(SystemExit, "too large to review safely"):
            self.helper["validate_review_patch"]("local staged diff", ["safe.txt"], "x" * 25, 10)

    def test_review_patch_limit_counts_utf8_bytes(self) -> None:
        with self.assertRaisesRegex(SystemExit, r"12 bytes; limit 10"):
            self.helper["validate_review_patch"]("local staged diff", ["safe.txt"], "界" * 4, 10)

    def test_review_patch_accepts_large_content_without_explicit_limit(self) -> None:
        patch = (
            "diff --git a/safe.txt b/safe.txt\n"
            "--- a/safe.txt\n"
            "+++ b/safe.txt\n"
            "@@ -0,0 +1,100000 @@\n"
            + "+safe review content\n" * 100_000
        )

        self.assertEqual(
            self.helper["validate_review_patch"](
                "local staged diff",
                ["safe.txt"],
                patch,
            ),
            patch,
        )

    def test_review_bundle_chunking_preserves_every_byte_and_diff_context(self) -> None:
        bundle = (
            "# Commit Diff\n\n"
            "diff --git a/safe.txt b/safe.txt\n"
            "--- a/safe.txt\n"
            "+++ b/safe.txt\n"
            "@@ -0,0 +1,200 @@\n"
            + "+safe review content\n" * 200
        )

        chunks = self.helper["split_review_bundle"](bundle, 300)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunk.content for chunk in chunks), bundle)
        self.assertTrue(all(len(chunk.content.encode("utf-8")) <= 300 for chunk in chunks))
        self.assertTrue(
            any(
                "+++ b/safe.txt" in chunk.context
                and "@@ -0,0 +1,200 @@" in chunk.context
                and "Continuation begins at new-file line" in chunk.context
                for chunk in chunks[1:]
            )
        )

    def test_untracked_markdown_headings_do_not_create_bundle_boundaries(self) -> None:
        bundle = (
            "# Untracked Files\n\n"
            "# Untracked File\n"
            'path: "notes.md"\n'
            'source-line 1: "# title\\n"\n'
            'source-line 2: "## section\\n"\n\n'
            "# Untracked File\n"
            'path: "todo.md"\n'
            'source-line 1: "# next\\n"'
        )

        units = self.helper["review_bundle_units"](bundle)

        self.assertEqual(len(units), 3)
        self.assertIn(r'source-line 2: "## section\n"', units[1])
        self.assertEqual("".join(units), bundle)

    def test_unicode_line_separators_do_not_create_bundle_boundaries(self) -> None:
        bundle = (
            "# Untracked Files\n\n"
            "# Untracked File\n"
            'path: "notes.txt"\n'
            'source-line 1: "before\u2028diff --git a/fake b/fake"\n\n'
            "diff --git a/real.txt b/real.txt\n"
            "--- a/real.txt\n"
            "+++ b/real.txt\n"
        )

        units = self.helper["review_bundle_units"](bundle)

        self.assertEqual(len(units), 3)
        self.assertIn("\u2028diff --git a/fake b/fake", units[1])
        self.assertEqual("".join(units), bundle)

    def test_diff_source_prefixes_do_not_replace_file_context(self) -> None:
        context: list[str] = []
        next_new_line = None
        next_old_line = None
        in_hunk = False
        lines = (
            "diff --git a/safe.txt b/safe.txt\n",
            "--- a/safe.txt\n",
            "+++ b/safe.txt\n",
            "@@ -10,2 +10,3 @@\n",
            "+++ added source beginning with pluses\n",
            "--- deleted source beginning with minuses\n",
            " context\n",
        )

        for line in lines:
            next_new_line, next_old_line, in_hunk = self.helper[
                "update_review_chunk_context"
            ](
                context,
                line,
                next_new_line,
                next_old_line,
                in_hunk,
            )

        self.assertEqual(next_new_line, 12)
        self.assertEqual(next_old_line, 12)
        self.assertIn("--- a/safe.txt\n", context)
        self.assertIn("+++ b/safe.txt\n", context)
        self.assertNotIn("--- deleted source beginning with minuses\n", context)

    def test_hunk_header_that_fits_fresh_chunk_is_not_split(self) -> None:
        unit = (
            "diff --git a/abcdefghijk b/abcdefghijk\n"
            "--- a/abcdefghijk\n"
            "+++ b/abcdefghijk\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

        chunks = self.helper["split_oversized_review_unit"](unit, 85)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(any("@@ -1 +1 @@\n" in chunk.content for chunk in chunks))
        self.assertEqual("".join(chunk.content for chunk in chunks), unit)

    def test_long_diff_line_continuations_keep_their_original_marker(self) -> None:
        for marker in ("+", "-", " "):
            with self.subTest(marker=marker):
                unit = (
                    "diff --git a/large.txt b/large.txt\n"
                    "--- a/large.txt\n"
                    "+++ b/large.txt\n"
                    "@@ -1 +1 @@\n"
                    f"{marker}{'x' * 400}\n"
                )

                chunks = self.helper["split_oversized_review_unit"](unit, 140)

                self.assertTrue(
                    any(
                        f"original marker is `{marker}`" in chunk.context
                        for chunk in chunks[1:]
                    )
                )
                self.assertEqual("".join(chunk.content for chunk in chunks), unit)

    def test_modified_file_deletion_context_keeps_old_and_new_offsets(self) -> None:
        context: list[str] = []
        next_new_line = None
        next_old_line = None
        in_hunk = False
        for line in (
            "diff --git a/safe.txt b/safe.txt\n",
            "--- a/safe.txt\n",
            "+++ b/safe.txt\n",
            "@@ -10,3 +10,2 @@\n",
            "-first deleted line\n",
        ):
            next_new_line, next_old_line, in_hunk = self.helper[
                "update_review_chunk_context"
            ](
                context,
                line,
                next_new_line,
                next_old_line,
                in_hunk,
            )

        rendered = self.helper["review_chunk_context"](
            context,
            next_new_line,
            next_old_line,
        )

        self.assertIn("new-file line 10", rendered)
        self.assertIn("old-file line 11", rendered)

    def test_multiple_long_line_tails_pack_into_following_chunks(self) -> None:
        limit = 200
        unit = (
            "diff --git a/large.txt b/large.txt\n"
            "--- a/large.txt\n"
            "+++ b/large.txt\n"
            "@@ -1,5 +1,5 @@\n"
            + ("+" + "x" * 205 + "\n") * 5
        )

        chunks = self.helper["split_oversized_review_unit"](unit, limit)
        minimum_chunks = (len(unit.encode("utf-8")) + limit - 1) // limit

        self.assertLessEqual(len(chunks), minimum_chunks + 1)
        self.assertEqual("".join(chunk.content for chunk in chunks), unit)
        self.assertTrue(all(len(chunk.content.encode("utf-8")) <= limit for chunk in chunks))

    def test_untracked_continuation_context_keeps_source_line(self) -> None:
        unit = (
            "# Untracked File\n"
            'path: "notes.txt"\n'
            'source-line 1: "short\\n"\n'
            f'source-line 2: "{"x" * 300}"\n'
        )

        chunks = self.helper["split_oversized_review_unit"](unit, 120)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(
            any(
                "Continuation begins at untracked source line 2" in chunk.context
                for chunk in chunks[1:]
            )
        )
        self.assertEqual("".join(chunk.content for chunk in chunks), unit)

    def test_deleted_file_continuation_uses_positive_old_line(self) -> None:
        unit = (
            "diff --git a/removed.txt b/removed.txt\n"
            "--- a/removed.txt\n"
            "+++ /dev/null\n"
            "@@ -40,50 +0,0 @@\n"
            + "-deleted content\n" * 50
        )

        chunks = self.helper["split_oversized_review_unit"](unit, 180)

        deletion_contexts = [
            chunk.context for chunk in chunks[1:] if "old-file line" in chunk.context
        ]
        self.assertTrue(deletion_contexts)
        self.assertTrue(all("line 0" not in context for context in deletion_contexts))
        self.assertTrue(all("--- a/removed.txt" in context for context in deletion_contexts))

    def test_long_complete_context_is_retained_or_rejected(self) -> None:
        path = "nested/" + "x" * 10_000 + ".txt"
        context = [
            f'diff --git "a/{path}" "b/{path}"\n',
            f'--- "a/{path}"\n',
            f'+++ "b/{path}"\n',
            "@@ -1 +1 @@\n",
        ]

        rendered = self.helper["review_chunk_context"](context, 2, 2)

        self.assertIn(f'+++ "b/{path}"', rendered)
        self.assertIn("@@ -1 +1 @@", rendered)
        self.assertIn("Continuation begins at new-file line 2", rendered)

    def test_review_bundle_packs_oversized_unit_tails_globally(self) -> None:
        limit = 1_000
        units = []
        for index in range(5):
            header = (
                f"diff --git a/file-{index}.txt b/file-{index}.txt\n"
                f"--- a/file-{index}.txt\n"
                f"+++ b/file-{index}.txt\n"
                "@@ -0,0 +1 @@\n"
            )
            body = "+" + "x" * (1_100 - len(header.encode("utf-8")) - 2) + "\n"
            units.append(header + body)
        bundle = "".join(units)

        chunks = self.helper["split_review_bundle"](bundle, limit)

        self.assertEqual(len(chunks), 6)
        self.assertEqual("".join(chunk.content for chunk in chunks), bundle)
        self.assertTrue(all(len(chunk.content.encode("utf-8")) <= limit for chunk in chunks))

    def test_large_bundle_stays_single_pass_until_prompt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            prompts = self.helper["build_review_prompts"](
                repo,
                "commit",
                "HEAD",
                "# Commit Diff\n" + "safe review content\n" * 18_000,
                "",
                "",
            )

        self.assertEqual(len(prompts), 1)

    def test_bundle_above_prompt_limit_uses_complete_bounded_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            prompts = self.helper["build_review_prompts"](
                repo,
                "commit",
                "HEAD",
                "# Commit Diff\n" + "safe review content\n" * 35_000,
                "",
                "",
            )

        self.assertGreater(len(prompts), 1)
        self.assertTrue(
            all(
                len(prompt.encode("utf-8"))
                <= self.helper["MAX_REVIEW_PROMPT_BYTES"]
                for prompt in prompts
            )
        )
        self.assertTrue(all("Oversized review bundle chunk:" in prompt for prompt in prompts))

    def test_review_prompt_preserves_bundle_ending_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            bundle = "# Commit Diff\n+Markdown hard break  \n+\n"
            prompt = self.helper["render_review_prompt"](
                repo,
                "commit",
                "HEAD",
                self.helper["ReviewChunk"](bundle),
                "",
                "",
            )

        self.assertTrue(prompt.endswith(bundle))

    def test_review_pass_count_is_bounded(self) -> None:
        builder = self.helper["build_review_prompts"]
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            with mock.patch.dict(builder.__globals__, {"MAX_REVIEW_PASSES": 1}):
                with self.assertRaisesRegex(SystemExit, "more than 1 bounded passes"):
                    builder(
                        repo,
                        "commit",
                        "HEAD",
                        "# Commit Diff\n" + "safe review content\n" * 35_000,
                        "",
                        "",
                    )

    def test_fallback_self_test_ignores_ambient_model_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "AUTOREVIEW_MODEL": "ambient-global-model",
                "AUTOREVIEW_CODEX_MODEL": "ambient-codex-model",
            },
            clear=False,
        ):
            self.helper["self_test_fallback_scope"]()

    def test_pi_refuses_truncated_review_input(self) -> None:
        reviewer = argparse.Namespace(engine="pi", tools=True)

        with self.assertRaisesRegex(SystemExit, "pi engine refused truncated review input"):
            self.helper["ensure_reviewer_input_complete"](
                reviewer,
                True,
            )

        self.helper["ensure_reviewer_input_complete"](
            reviewer,
            False,
        )
        with self.assertRaisesRegex(SystemExit, "codex engine refused truncated review input"):
            self.helper["ensure_reviewer_input_complete"](
                argparse.Namespace(engine="codex", tools=True),
                True,
            )
        with self.assertRaisesRegex(SystemExit, "claude engine refused truncated review input"):
            self.helper["ensure_reviewer_input_complete"](
                argparse.Namespace(engine="claude", tools=True),
                True,
            )
        with self.assertRaisesRegex(SystemExit, "droid engine refused truncated review input"):
            self.helper["ensure_reviewer_input_complete"](
                argparse.Namespace(engine="droid", tools=False),
                True,
            )

    def test_safe_git_env_preserves_trusted_platform_and_helper_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            repo_bin = repo / "bin"
            trusted_bin = root / "trusted-bin"
            repo_bin.mkdir()
            trusted_bin.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": os.pathsep.join((str(repo_bin), str(trusted_bin))),
                    "SYSTEMROOT": "C:\\Windows",
                    "GIT_DIR": str(repo / ".git"),
                    "OPENAI_API_KEY": "must-not-reach-git",
                },
                clear=False,
            ):
                env = self.helper["safe_git_env"](repo)

        self.assertNotIn(str(repo_bin.resolve()), env["PATH"].split(os.pathsep))
        self.assertIn(str(trusted_bin.resolve()), env["PATH"].split(os.pathsep))
        self.assertEqual(env["SYSTEMROOT"], "C:\\Windows")
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_boolean_environment_values_fail_closed(self) -> None:
        with mock.patch.dict(os.environ, {"AUTOREVIEW_TEST_BOOL": "flase"}):
            with self.assertRaisesRegex(SystemExit, "invalid boolean environment value"):
                self.helper["env_truthy"]("AUTOREVIEW_TEST_BOOL")

    def test_droid_fails_closed_without_complete_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "AGENTS.md").write_text("hostile instructions\n", encoding="utf-8")

            with self.assertRaisesRegex(
                SystemExit,
                r"droid engine is unavailable.*use codex, claude, or pi",
            ) as error:
                self.helper["run_droid"](argparse.Namespace(), repo, "prompt")
            self.assertNotIn("opencode", str(error.exception))

    def test_prompt_file_keeps_recoverable_repo_path(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            (repo / "review.md").write_text("review context\n", encoding="utf-8")
            args = argparse.Namespace(prompt=[], prompt_file=["review.md"])

            prompt, truncated = self.helper["load_extra_prompt"](args, repo)

            self.assertIn("# Prompt file: review.md", prompt)
            self.assertFalse(truncated)

    def test_build_prompt_omits_absolute_repo_path_and_caps_aggregate_input(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            prompt = self.helper["build_prompt"](repo, "local", None, "diff", "", "")

            self.assertIn(
                "Review sandbox: . (intentionally contains no reviewed repository files)",
                prompt,
            )
            self.assertIn("Read-only tools cannot access unchanged repository files", prompt)
            self.assertIn(
                "Do not report a missing import, symbol, definition, call site, config entry",
                prompt,
            )
            self.assertNotIn(str(repo), prompt)
            with self.assertRaisesRegex(SystemExit, "aggregate limit"):
                self.helper["build_prompt"](
                    repo,
                    "local",
                    None,
                    "x" * self.helper["MAX_REVIEW_PROMPT_BYTES"],
                    "",
                    "",
                )

    def test_cursor_refuses_global_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            global_mcp = root / ".cursor" / "mcp.json"
            global_mcp.parent.mkdir()
            global_mcp.write_text("{}\n", encoding="utf-8")
            args = argparse.Namespace(
                thinking=None,
                tools=True,
                web_search=True,
                cursor_allow_workspace_instructions=True,
            )

            with mock.patch.object(Path, "home", return_value=root), mock.patch.dict(
                os.environ,
                {"HOME": str(root), "USERPROFILE": str(root)},
            ):
                with self.assertRaisesRegex(SystemExit, "cursor engine is unavailable"):
                    self.helper["run_cursor"](args, repo, "prompt")

    def test_cursor_refuses_user_level_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir()
            settings.write_text('{"hooks":{"PreToolUse":[{"command":"unsafe"}]}}\n', encoding="utf-8")
            args = argparse.Namespace(
                thinking=None,
                tools=True,
                web_search=True,
                cursor_allow_workspace_instructions=True,
            )

            with mock.patch.object(Path, "home", return_value=root), mock.patch.dict(
                os.environ,
                {"HOME": str(root), "USERPROFILE": str(root)},
            ):
                with self.assertRaisesRegex(SystemExit, "cursor engine is unavailable"):
                    self.helper["run_cursor"](args, repo, "prompt")

            settings.write_text('{"permissions":{"allow":["Read(**)"]}}\n', encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=root), mock.patch.dict(
                os.environ,
                {"HOME": str(root), "USERPROFILE": str(root)},
            ):
                self.assertEqual(self.helper["cursor_global_hook_paths"](), [])

            settings.write_text('{"enabledPlugins":{"review-hooks@example":true}}\n', encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=root), mock.patch.dict(
                os.environ,
                {"HOME": str(root), "USERPROFILE": str(root)},
            ):
                self.assertEqual(self.helper["cursor_global_hook_paths"](), [settings])

    def test_read_text_truncates_without_scanning_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "large.txt"
            path.write_bytes(b"x" * 200_000 + b"\0tail")

            text = self.helper["read_text"](path)

            self.assertIn("[truncated at 180000 characters]", text)
            self.assertNotEqual(text, "[binary file omitted]")

    def test_read_text_marks_unreadable_input_incomplete(self) -> None:
        with mock.patch.dict(
            self.helper["read_text_with_status"].__globals__,
            {"read_prefix": lambda *_args: (_ for _ in ()).throw(SystemExit("denied"))},
        ):
            text, incomplete = self.helper["read_text_with_status"](Path("blocked"))

        self.assertIn("[unreadable:", text)
        self.assertTrue(incomplete)

    def test_evidence_file_must_be_repo_relative_and_not_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            outside = root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "repo-relative"):
                self.helper["validate_evidence_file"](repo, str(outside), "--prompt-file")

            target = repo / "notes.md"
            target.write_text("notes\n", encoding="utf-8")
            link = repo / "link.md"
            try:
                link.symlink_to(target)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is not available")
                raise
            with self.assertRaisesRegex(SystemExit, "symlinked"):
                self.helper["validate_evidence_file"](repo, "link.md", "--dataset")

    def test_safe_engine_env_strips_process_injection_variables(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            try:
                os.environ["GIT_DIR"] = "/tmp/unsafe-git-dir"
                os.environ["GIT_CONFIG_COUNT"] = "99"
                os.environ["DYLD_INSERT_LIBRARIES"] = "/tmp/unsafe.dylib"
                os.environ["NODE_OPTIONS"] = "--require=/tmp/unsafe.js"
                os.environ["NODE_PATH"] = "/tmp/unsafe-node"
                os.environ["LD_AUDIT"] = "/tmp/unsafe-audit.so"
                os.environ["LD_LIBRARY_PATH"] = "/tmp/unsafe-lib"
                os.environ["RUBYOPT"] = "-r/tmp/unsafe.rb"
                os.environ["PERL5OPT"] = "-Munsafe"
                os.environ["BUN_OPTIONS"] = "--preload=/tmp/unsafe.js"
                os.environ["OPENCODE_CONFIG"] = "/tmp/unsafe-opencode.json"
                os.environ["OPENCODE_PERMISSION"] = "allow"
                os.environ["OPENCODE_AUTO_SHARE"] = "1"
                os.environ["COPILOT_ALLOW_ALL"] = "1"
                os.environ["CODEX_HOME"] = "/tmp/codex-auth"
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"
                os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"
                os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/claude-auth"
                os.environ["PI_CODING_AGENT_DIR"] = "/tmp/pi-auth"
                os.environ["CLAUDE_CODE_USE_FOUNDRY"] = "1"
                os.environ["CLOUD_ML_REGION"] = "us-east5"
                os.environ["ANTHROPIC_AUTH_TOKEN"] = "test-auth-token"
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = "test-token-placeholder"
                os.environ["ANTHROPIC_BEDROCK_BASE_URL"] = (
                    "https://bedrock.example.invalid"
                )
                os.environ["ANTHROPIC_VERTEX_BASE_URL"] = (
                    "https://vertex.example.invalid"
                )
                os.environ["AWS_PROFILE"] = "review-profile"
                os.environ["AWS_CONFIG_FILE"] = "/tmp/unsafe-aws-config"
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
                    "/tmp/unsafe-google-credentials"
                )
                os.environ["GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES"] = "1"
                os.environ["OPENROUTER_API_KEY"] = "test-provider-key"
                os.environ["GITHUB_TOKEN"] = "test-token-placeholder"
                os.environ["HTTPS_PROXY"] = "http://proxy.example.invalid:8080"
                os.environ["HTTP_PROXY"] = "proxy.example.invalid:8080"
                os.environ["ALL_PROXY"] = "socks5://proxy.example.invalid:1080"
                os.environ["DO_NOT_TRACK"] = "1"
                os.environ["DISABLE_TELEMETRY"] = "1"
                os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

                env = self.helper["safe_engine_env"](repo, engine="codex")
                claude_env = self.helper["safe_engine_env"](repo, engine="claude")
                pi_env = self.helper["safe_engine_env"](repo, engine="pi")

                self.assertNotEqual(env.get("GIT_DIR"), "/tmp/unsafe-git-dir")
                self.assertEqual(
                    env["GIT_CONFIG_COUNT"],
                    str(len(self.helper["ENGINE_GIT_CONFIG_OVERRIDES"])),
                )
                self.assertNotIn("DYLD_INSERT_LIBRARIES", env)
                self.assertNotIn("NODE_OPTIONS", env)
                for key in (
                    "NODE_PATH",
                    "LD_AUDIT",
                    "LD_LIBRARY_PATH",
                    "RUBYOPT",
                    "PERL5OPT",
                    "BUN_OPTIONS",
                    "OPENCODE_CONFIG",
                    "OPENCODE_PERMISSION",
                    "OPENCODE_AUTO_SHARE",
                ):
                    self.assertNotIn(key, env)
                self.assertNotIn("COPILOT_ALLOW_ALL", env)
                self.assertNotIn("GITHUB_TOKEN", env)
                self.assertEqual(env["HTTPS_PROXY"], "http://proxy.example.invalid:8080")
                self.assertEqual(env["HTTP_PROXY"], "proxy.example.invalid:8080")
                self.assertEqual(env["ALL_PROXY"], "socks5://proxy.example.invalid:1080")
                self.assertEqual(env["DO_NOT_TRACK"], "1")
                self.assertEqual(env["DISABLE_TELEMETRY"], "1")
                self.assertEqual(env["CODEX_HOME"], "/tmp/codex-auth")
                if os.name == "nt":
                    self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
                else:
                    self.assertEqual(
                        env["DBUS_SESSION_BUS_ADDRESS"],
                        "unix:path=/run/user/1000/bus",
                    )
                self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
                self.assertEqual(
                    claude_env["CLAUDE_CONFIG_DIR"],
                    "/tmp/claude-auth",
                )
                self.assertEqual(
                    claude_env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"],
                    "1",
                )
                self.assertEqual(pi_env["PI_CODING_AGENT_DIR"], "/tmp/pi-auth")
                self.assertEqual(claude_env["CLAUDE_CODE_USE_FOUNDRY"], "1")
                self.assertEqual(claude_env["CLOUD_ML_REGION"], "us-east5")
                self.assertEqual(
                    claude_env["ANTHROPIC_AUTH_TOKEN"],
                    "test-auth-token",
                )
                self.assertEqual(
                    claude_env["AWS_BEARER_TOKEN_BEDROCK"],
                    "test-token-placeholder",
                )
                self.assertEqual(
                    claude_env["ANTHROPIC_BEDROCK_BASE_URL"],
                    "https://bedrock.example.invalid",
                )
                self.assertEqual(
                    claude_env["ANTHROPIC_VERTEX_BASE_URL"],
                    "https://vertex.example.invalid",
                )
                self.assertEqual(claude_env["AWS_PROFILE"], "review-profile")
                self.assertNotIn("AWS_CONFIG_FILE", env)
                self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
                self.assertNotIn(
                    "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES",
                    env,
                )
                self.assertNotIn("OPENROUTER_API_KEY", env)
                self.assertEqual(
                    claude_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"],
                    "1",
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_parallel_tests_use_sanitized_environment_for_every_shell(self) -> None:
        observed: list[dict[str, object]] = []
        sanitized_env = {
            "PATH": "/usr/bin",
            "HOME": "/safe/home",
            "JAVA_TOOL_OPTIONS": "'-Duser.home=/safe/home'",
        }

        def fake_popen(command: object, **kwargs: object) -> mock.Mock:
            observed.append({"command": command, **kwargs})
            proc = mock.Mock()
            proc.returncode = 0
            proc.stderr = io.StringIO("")
            return proc

        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            with mock.patch.dict(
                self.helper["start_parallel_tests"].__globals__,
                {
                    "safe_test_env": lambda actual_repo, test_home: (
                        sanitized_env
                        if actual_repo == repo and not test_home.is_relative_to(repo)
                        else self.fail("parallel tests sanitized the wrong repository")
                    ),
                    "resolve_command": lambda name, actual_repo: (
                        f"/usr/bin/{name}"
                        if actual_repo == repo
                        else self.fail("parallel tests resolved a shell for the wrong repository")
                    ),
                },
            ), mock.patch("subprocess.Popen", side_effect=fake_popen):
                for shell_kind in ("default", "cmd", "powershell", "pwsh"):
                    proc, started = self.helper["start_parallel_tests"](
                        "run tests", repo, shell_kind
                    )
                    test_home = getattr(proc, "_autoreview_test_home")
                    self.assertTrue(test_home.is_dir())
                    self.helper["finish_parallel_tests"](proc, started)
                    self.assertFalse(test_home.exists())

        self.assertEqual(len(observed), 4)
        for invocation in observed:
            self.assertEqual(invocation["cwd"], repo)
            self.assertEqual(invocation["env"], sanitized_env)
            self.assertEqual(invocation["stderr"], subprocess.PIPE)
            self.assertTrue(invocation["text"])
        self.assertTrue(observed[0]["shell"])
        self.assertTrue(observed[1]["shell"])
        self.assertNotIn("shell", observed[2])
        self.assertNotIn("shell", observed[3])

    def test_parallel_test_finish_does_not_wait_for_inherited_stderr_pipe(
        self,
    ) -> None:
        release = threading.Event()
        stderr_thread = threading.Thread(target=release.wait, daemon=True)
        stderr_thread.start()
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                test_home = Path(tempdir) / "test-home"
                test_home.mkdir()
                proc = mock.Mock()
                proc.returncode = 0
                proc.wait.return_value = 0
                setattr(proc, "_autoreview_test_home", test_home)
                setattr(proc, "_autoreview_stderr_thread", stderr_thread)

                started = time.time()
                before = time.monotonic()
                result = self.helper["finish_parallel_tests"](proc, started)
                elapsed = time.monotonic() - before

                self.assertEqual(result, 0)
                self.assertLess(elapsed, 1)
                self.assertFalse(test_home.exists())
        finally:
            release.set()
            stderr_thread.join(timeout=1)

    def test_source_tree_snapshot_detects_parallel_test_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            source = repo / "source.txt"
            source.write_text("before\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            git(repo, "commit", "-qm", "initial")
            before = self.helper["source_tree_snapshot"](repo)

            source.write_text("after\n", encoding="utf-8")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )
            source.write_text("before\n", encoding="utf-8")
            self.assertEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

            source.write_text("after\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            git(repo, "commit", "-qm", "mutated")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

            (repo / "generated.txt").write_text("generated\n", encoding="utf-8")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

    def test_rejects_output_paths_inside_reviewed_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            outside = root / "outside.json"

            with self.assertRaisesRegex(
                SystemExit,
                "--json-output must point outside",
            ):
                self.helper["reject_repo_output_paths"](
                    argparse.Namespace(
                        json_output=str(repo / "review.json"),
                        output=None,
                    ),
                    repo,
                )
            with self.assertRaisesRegex(
                SystemExit,
                "--output must point outside",
            ):
                self.helper["reject_repo_output_paths"](
                    argparse.Namespace(
                        json_output=None,
                        output=str(repo / "review.txt"),
                    ),
                    repo,
                )

            self.helper["reject_repo_output_paths"](
                argparse.Namespace(
                    json_output=str(outside),
                    output=None,
                ),
                repo,
            )
            alternate_repo = repo.with_name(repo.name.swapcase())
            with (
                mock.patch.object(
                    os.path,
                    "samefile",
                    side_effect=lambda left, right: (
                        str(left).casefold() == str(right).casefold()
                    ),
                ),
                self.assertRaisesRegex(
                    SystemExit,
                    "--json-output must point outside",
                ),
            ):
                self.helper["reject_repo_output_paths"](
                    argparse.Namespace(
                        json_output=str(alternate_repo / "review.json"),
                        output=None,
                    ),
                    repo,
                )

    def test_atomic_output_replaces_hard_link_without_touching_repo_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            tracked = repo / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            outside = root / "review.txt"
            os.link(tracked, outside)

            self.helper["atomic_write_text"](outside, "review\n")

            self.assertEqual(
                tracked.read_text(encoding="utf-8"),
                "tracked\n",
            )
            self.assertEqual(
                outside.read_text(encoding="utf-8"),
                "review\n",
            )
            self.assertFalse(os.path.samefile(tracked, outside))

    def test_partial_panel_failure_output_is_terminal_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            reviewers = [
                argparse.Namespace(
                    engine="codex",
                    model=None,
                    fallback_model=None,
                    thinking=None,
                ),
                argparse.Namespace(
                    engine="claude",
                    model=None,
                    fallback_model=None,
                    thinking=None,
                ),
            ]
            args = argparse.Namespace(
                allow_partial_panel=True,
                require_finding=[],
            )
            report = {
                "findings": [],
                "overall_correctness": "patch is correct",
                "overall_explanation": "clean",
                "overall_confidence": 0.9,
            }

            def run_reviewer(reviewer: argparse.Namespace, *_args: object) -> object:
                if reviewer.engine == "claude":
                    raise RuntimeError(
                        "\x1b]8;;https://example.invalid\x07click"
                        "\x1b]8;;\x07"
                    )
                return report

            stdout = io.StringIO()
            with (
                mock.patch.dict(
                    self.helper["run_panel"].__globals__,
                    {"run_reviewer": run_reviewer},
                ),
                contextlib.redirect_stdout(stdout),
            ):
                self.helper["run_panel"](
                    args,
                    reviewers,
                    repo,
                    "prompt",
                    set(),
                    False,
                )

            output = stdout.getvalue()
            self.assertNotIn("\x1b", output)
            self.assertNotIn("\x07", output)
            self.assertIn("\\x1b]8;;", output)
            self.assertIn("\\x07", output)

    def test_fatal_panel_failure_output_is_terminal_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            reviewers = [
                argparse.Namespace(
                    engine="codex",
                    model=None,
                    fallback_model=None,
                    thinking=None,
                )
            ]
            args = argparse.Namespace(
                allow_partial_panel=False,
                require_finding=[],
            )

            def run_reviewer(*_args: object) -> object:
                raise RuntimeError("\x1b]8;;https://example.invalid\x07click")

            with (
                mock.patch.dict(
                    self.helper["run_panel"].__globals__,
                    {"run_reviewer": run_reviewer},
                ),
                self.assertRaises(SystemExit) as error,
            ):
                self.helper["run_panel"](
                    args,
                    reviewers,
                    repo,
                    "prompt",
                    set(),
                    False,
                )

            message = str(error.exception)
            self.assertNotIn("\x1b", message)
            self.assertNotIn("\x07", message)
            self.assertIn("\\x1b]8;;", message)
            self.assertIn("\\x07", message)

    def test_source_tree_snapshot_supports_staged_files_before_first_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            source = repo / "source.txt"
            source.write_text("before\n", encoding="utf-8")
            git(repo, "add", "source.txt")

            before = self.helper["source_tree_snapshot"](repo)
            symbolic_head = git(repo, "symbolic-ref", "HEAD").strip()
            self.assertEqual(before[0], f"unborn:{symbolic_head}")

            git(repo, "symbolic-ref", "HEAD", "refs/heads/other")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )
            git(repo, "symbolic-ref", "HEAD", symbolic_head)

            source.write_text("after\n", encoding="utf-8")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

    @unittest.skipIf(os.name == "nt", "the true command is POSIX-only")
    def test_cli_parallel_tests_supports_unborn_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source = repo / "source.txt"
            source.write_text("staged\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            codex_bin = self.helper["write_executable"](
                root / "codex",
                self.helper["fake_codex_script"](),
            )
            record_path = root / "record.json"
            env = os.environ.copy()
            env.update(
                {
                    "AUTOREVIEW_FAKE_RECORD": str(record_path),
                    "HOME": str(root),
                    "USERPROFILE": str(root),
                }
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "local",
                    "--engine",
                    "codex",
                    "--codex-bin",
                    str(codex_bin),
                    "--parallel-tests",
                    "true",
                ],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("autoreview clean", result.stdout)

    @unittest.skipIf(os.name == "nt", "the fake executable is POSIX-only")
    def test_cli_detects_source_mutation_without_parallel_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source = repo / "source.txt"
            source.write_text("before\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            git(repo, "commit", "-qm", "initial")
            source.write_text("review me\n", encoding="utf-8")
            codex_bin = self.helper["write_executable"](
                root / "codex",
                self.helper["fake_codex_script"](),
            )
            record_path = root / "record.json"
            env = os.environ.copy()
            env.update(
                {
                    "AUTOREVIEW_FAKE_MUTATE": str(source),
                    "AUTOREVIEW_FAKE_RECORD": str(record_path),
                    "HOME": str(root),
                    "USERPROFILE": str(root),
                }
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "local",
                    "--engine",
                    "codex",
                    "--codex-bin",
                    str(codex_bin),
                ],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "source changed after the review bundle was created",
                result.stderr,
            )
            self.assertTrue(record_path.is_file())

    def test_source_tree_snapshot_hashes_binary_and_untracked_tail_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            tracked = repo / "tracked.bin"
            tracked.write_bytes(b"\0tracked-before")
            git(repo, "add", "tracked.bin")
            git(repo, "commit", "-qm", "initial")
            limit = self.helper["MAX_BUNDLE_TEXT_BYTES"]
            untracked = repo / "generated.bin"
            untracked.write_bytes(b"\0" + b"a" * (limit + 16))
            before = self.helper["source_tree_snapshot"](repo)

            tracked.write_bytes(b"\0tracked-after!")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )
            tracked.write_bytes(b"\0tracked-before")
            self.assertEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

            with untracked.open("r+b") as stream:
                stream.seek(-1, os.SEEK_END)
                stream.write(b"b")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

    def test_source_tree_snapshot_includes_index_state(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            source = repo / "source.txt"
            source.write_text("before\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            git(repo, "commit", "-qm", "initial")
            before = self.helper["source_tree_snapshot"](repo)

            source.write_text("staged\n", encoding="utf-8")
            git(repo, "add", "source.txt")
            source.write_text("before\n", encoding="utf-8")
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

    def test_source_tree_snapshot_includes_tracked_submodule_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            child = root / "child"
            child.mkdir()
            git(child, "init", "-q")
            source = child / "source.txt"
            source.write_text("before\n", encoding="utf-8")
            git(child, "add", "source.txt")
            git(child, "commit", "-qm", "initial")

            repo = init_repo(root)
            git(
                repo,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(child),
                "vendor/dependency",
            )
            git(repo, "commit", "-qam", "add submodule")
            before = self.helper["source_tree_snapshot"](repo)

            (repo / "vendor/dependency/source.txt").write_text(
                "after\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                self.helper["source_tree_snapshot"](repo),
                before,
            )

    def test_trusted_maintainer_testbox_preserves_only_credentials(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            isolated_home = root / "test-home"
            host_home = root / "host-home"
            rustup_home = host_home / ".rustup"
            rustup_home.mkdir(parents=True)
            blacksmith_home = host_home / ".blacksmith"
            blacksmith_home.mkdir()
            blacksmith_credentials = blacksmith_home / "credentials"
            blacksmith_credentials.write_bytes(b"test-blacksmith-credentials")
            (blacksmith_home / "unrelated-state").write_text(
                "do not copy",
                encoding="utf-8",
            )
            local_bin = repo / ".venv" / "bin"
            local_bin.mkdir(parents=True)
            try:
                os.environ["PATH"] = f"{local_bin}{os.pathsep}/usr/bin"
                os.environ["CI"] = "1"
                os.environ["GRADLE_USER_HOME"] = "/host/gradle"
                os.environ["HOME"] = str(host_home)
                os.environ["JAVA_HOME"] = "/opt/jdk"
                os.environ["JAVA_TOOL_OPTIONS"] = "-javaagent:/host/unsafe.jar"
                os.environ["NODE_ENV"] = "test"
                os.environ["OPENCLAW_TESTBOX"] = "1"
                os.environ["PROJECT_FEATURE_MODE"] = "strict"
                os.environ["GH_CONFIG_DIR"] = "/host/gh"
                os.environ["CLOUDSDK_CONFIG"] = "/host/gcloud"
                os.environ["XDG_CONFIG_HOME"] = "/host/xdg"
                os.environ["GITHUB_TOKEN"] = "test-token-placeholder"
                os.environ["AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"] = (
                    "/host/aws-token"
                )
                os.environ["AZURE_FEDERATED_TOKEN_FILE"] = "/host/azure-token"
                os.environ["CI_JOB_JWT"] = "header.payload.signature"
                os.environ["DOCKER_AUTH_CONFIG"] = '{"auths":{"registry":{}}}'
                os.environ["PGPASSFILE"] = "/host/pgpass"
                os.environ["PGPASSWORD"] = "short-password"
                os.environ["REDISCLI_AUTH"] = "short-password"
                os.environ["BASH_FUNC_testcmd%%"] = "() { echo injected; }"
                os.environ["SHELLOPTS"] = "xtrace"
                os.environ["NODE_OPTIONS"] = "--require=/tmp/unsafe.js"
                os.environ["SERVICE_URL"] = (
                    "https://review-user:review-password@example.invalid/api"
                )
                os.environ["UNRELATED_VALUE"] = "ghp_" + "A" * 24

                env = self.helper["safe_test_env"](repo, isolated_home)

                self.assertEqual(env["PATH"], os.environ["PATH"])
                self.assertEqual(env["CI"], "1")
                self.assertEqual(
                    env["GRADLE_USER_HOME"],
                    str((isolated_home / ".gradle").resolve()),
                )
                self.assertEqual(env["JAVA_HOME"], "/opt/jdk")
                self.assertEqual(
                    env["JAVA_TOOL_OPTIONS"],
                    self.helper["quote_java_tool_option"](
                        f"-Duser.home={isolated_home.resolve()}"
                    ),
                )
                self.assertEqual(env["NODE_ENV"], "test")
                self.assertEqual(env["OPENCLAW_TESTBOX"], "1")
                isolated_blacksmith = isolated_home / ".blacksmith"
                self.assertEqual(
                    (isolated_blacksmith / "credentials").read_bytes(),
                    b"test-blacksmith-credentials",
                )
                self.assertFalse(
                    (isolated_blacksmith / "unrelated-state").exists()
                )
                if os.name != "nt":
                    self.assertEqual(
                        stat.S_IMODE(
                            (isolated_blacksmith / "credentials").stat().st_mode
                        ),
                        0o600,
                    )
                self.assertNotIn("PROJECT_FEATURE_MODE", env)
                self.assertEqual(env["HOME"], str(isolated_home.resolve()))
                self.assertNotIn("CARGO_HOME", env)
                self.assertEqual(env["RUSTUP_HOME"], str(rustup_home.resolve()))
                self.assertEqual(
                    env["XDG_CONFIG_HOME"],
                    str(isolated_home.resolve() / ".config"),
                )
                self.assertNotIn("GH_CONFIG_DIR", env)
                self.assertNotIn("CLOUDSDK_CONFIG", env)
                self.assertNotIn("GITHUB_TOKEN", env)
                self.assertNotIn("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", env)
                self.assertNotIn("AZURE_FEDERATED_TOKEN_FILE", env)
                self.assertNotIn("CI_JOB_JWT", env)
                self.assertNotIn("DOCKER_AUTH_CONFIG", env)
                self.assertNotIn("PGPASSFILE", env)
                self.assertNotIn("PGPASSWORD", env)
                self.assertNotIn("REDISCLI_AUTH", env)
                self.assertNotIn("BASH_FUNC_testcmd%%", env)
                self.assertNotIn("SHELLOPTS", env)
                self.assertNotIn("NODE_OPTIONS", env)
                self.assertNotIn("SERVICE_URL", env)
                self.assertNotIn("UNRELATED_VALUE", env)

                os.environ.pop("HOME")
                os.environ["USERPROFILE"] = str(host_home)
                windows_env = self.helper["safe_test_env"](
                    repo,
                    root / "windows-test-home",
                )
                self.assertNotIn("CARGO_HOME", windows_env)
                self.assertEqual(
                    windows_env["RUSTUP_HOME"],
                    str(rustup_home.resolve()),
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_parallel_test_environment_isolates_jvm_user_home(self) -> None:
        java = shutil.which("java")
        if java is None:
            self.skipTest("java is not installed")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            isolated_home = root / "test home"
            env = self.helper["safe_test_env"](repo, isolated_home)

            result = subprocess.run(
                [java, "-XshowSettings:properties", "-version"],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            user_home = next(
                (
                    line.split("=", 1)[1].strip()
                    for line in result.stderr.splitlines()
                    if line.strip().startswith("user.home =")
                ),
                None,
            )
            self.assertEqual(user_home, str(isolated_home.resolve()))

    def test_parallel_test_stderr_relay_hides_only_our_java_banner(self) -> None:
        option = self.helper["quote_java_tool_option"](
            "-Duser.home=/tmp/test home"
        )
        stream = io.StringIO(
            f"Picked up JAVA_TOOL_OPTIONS: {option}\n"
            "ordinary stderr\n"
            f"Picked up JAVA_TOOL_OPTIONS: {option} -Dextra=true\n"
        )
        output = io.StringIO()

        with mock.patch("sys.stderr", output):
            self.helper["relay_parallel_test_stderr"](stream, option)

        self.assertEqual(
            output.getvalue(),
            "ordinary stderr\n"
            f"Picked up JAVA_TOOL_OPTIONS: {option} -Dextra=true\n",
        )

    def test_java_tool_option_quote_round_trips_special_paths(self) -> None:
        java = shutil.which("java")
        if java is None:
            self.skipTest("java is not installed")
        names = ["space home", "apostrophe's home"]
        if os.name != "nt":
            names.append('double"quote home')
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tempdir:
                home = Path(tempdir) / name
                home.mkdir()
                env = os.environ.copy()
                env["JAVA_TOOL_OPTIONS"] = self.helper["quote_java_tool_option"](
                    f"-Duser.home={home}"
                )
                result = subprocess.run(
                    [java, "-XshowSettings:properties", "-version"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"user.home = {home}", result.stderr)

    def test_safe_proxy_url_accepts_credential_free_formats(self) -> None:
        for value in (
            "http://proxy.example.invalid:8080",
            "proxy.example.invalid:8080",
            "socks4://proxy.example.invalid",
            "socks4a://proxy.example.invalid",
        ):
            with self.subTest(value=value):
                self.assertTrue(self.helper["safe_proxy_url"](value))

        for value in (
            "http://review-user:review-password@proxy.example.invalid:8080",
            "socks5://review-user:review-password@proxy.example.invalid:1080",
        ):
            with self.subTest(value=value):
                self.assertFalse(self.helper["safe_proxy_url"](value))

    def test_safe_engine_env_rejects_credentialed_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.dict(
            os.environ,
            {
                "HTTPS_PROXY": (
                    "http://review-user:review-password@proxy.example.invalid:8080"
                )
            },
            clear=False,
        ):
            repo = init_repo(Path(tempdir))
            with self.assertRaisesRegex(SystemExit, "credentialed or malformed proxy"):
                self.helper["safe_engine_env"](repo, engine="codex")

    def test_safe_temp_root_rejects_reviewed_repo_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            hostile_temp = repo / "tmp"
            hostile_temp.mkdir()

            with mock.patch.object(
                tempfile,
                "gettempdir",
                return_value=str(hostile_temp),
            ), self.assertRaisesRegex(
                SystemExit,
                "temporary directory must be outside",
            ):
                self.helper["safe_temp_root"](repo)

    @unittest.skipIf(os.name == "nt", "POSIX Testbox temp-root behavior")
    def test_testbox_parallel_test_temp_root_stays_within_socket_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            long_temp = root / ("macos-temp-root-" + "x" * 96)
            long_temp.mkdir()

            with mock.patch.object(
                tempfile,
                "gettempdir",
                return_value=str(long_temp),
            ), mock.patch.dict(
                os.environ,
                {"OPENCLAW_TESTBOX": "1"},
            ):
                selected = self.helper["parallel_test_temp_root"](repo)

            self.assertEqual(selected, Path("/tmp").resolve())
            socket_path = (
                selected
                / ("autoreview-test-home-" + "x" * 8)
                / ".blacksmith"
                / "c"
                / "6d146d2f25180c1d.sock"
            )
            self.assertLess(len(os.fsencode(socket_path)), 104)

    def test_parallel_test_temp_root_keeps_configured_root_without_testbox(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            configured_temp = root / "configured-temp"
            configured_temp.mkdir()

            with mock.patch.object(
                tempfile,
                "gettempdir",
                return_value=str(configured_temp),
            ), mock.patch.dict(
                os.environ,
                {"OPENCLAW_TESTBOX": "0"},
            ):
                selected = self.helper["parallel_test_temp_root"](repo)

            self.assertEqual(selected, configured_temp.resolve())

    def test_claude_fable_alias_requires_fable_safe_mode_version(self) -> None:
        args = argparse.Namespace(
            claude_bin="claude",
            fallback_model=None,
            model="fable",
        )
        version_result = subprocess.CompletedProcess(
            ["claude", "--version"],
            0,
            "2.1.169 (Claude Code)",
            "",
        )

        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            with mock.patch.dict(
                self.helper["ensure_claude_isolation_supported"].__globals__,
                {
                    "resolve_command": lambda *_args: "/usr/bin/claude",
                    "safe_engine_env": lambda *_args, **_kwargs: {},
                    "safe_temp_root": lambda _repo: Path(tempdir),
                    "run": lambda *_args, **_kwargs: version_result,
                },
            ), self.assertRaisesRegex(
                SystemExit,
                "2.1.170",
            ):
                self.helper["ensure_claude_isolation_supported"](args, repo)

    def test_claude_runs_outside_repo_with_auto_memory_disabled(self) -> None:
        args = argparse.Namespace(
            claude_allowed_tools=None,
            claude_bin="claude",
            fallback_model=None,
            model=None,
            stream_engine_output=False,
            thinking=None,
            tools=False,
            web_search=False,
        )
        observed: dict[str, object] = {}

        def fake_run(
            _cmd: list[str],
            cwd: Path,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            observed["cwd"] = cwd
            observed["env"] = kwargs["env"]
            return subprocess.CompletedProcess([], 0, "{}", "")

        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            with mock.patch.dict(
                self.helper["run_claude"].__globals__,
                {
                    "ensure_claude_isolation_supported": lambda *_args: None,
                    "resolve_command": lambda *_args: "/usr/bin/claude",
                    "run_with_heartbeat": fake_run,
                    "safe_engine_env": lambda *_args, **_kwargs: {
                        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"
                    },
                },
            ):
                self.helper["run_claude"](args, repo, "prompt")

            self.assertFalse(
                self.helper["is_within"](observed["cwd"], repo.resolve())
            )
            self.assertEqual(
                observed["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"],
                "1",
            )

    def test_codex_env_rejects_executable_dbus_transport(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            try:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = (
                    "unixexec:path=/tmp/hostile-helper"
                )
                env = self.helper["safe_engine_env"](repo, engine="codex")
                self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_multi_provider_engines_preserve_provider_auth(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir).resolve()
            repo = init_repo(root)
            try:
                os.environ["DEEPSEEK_API_KEY"] = "test-token-placeholder"
                os.environ["CEREBRAS_API_KEY"] = "test-token-placeholder"
                os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
                os.environ["CLOUDFLARE_API_TOKEN"] = "test-token-placeholder"
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
                    str(root / "provider-credentials.json")
                )
                os.environ["AWS_ROLE_ARN"] = (
                    "arn:aws:iam::123456789012:role/autoreview"
                )
                os.environ["AWS_CONTAINER_AUTHORIZATION_TOKEN"] = (
                    "test-token-placeholder"
                )
                os.environ["AWS_CONTAINER_CREDENTIALS_FULL_URI"] = (
                    "http://169.254.170.2/credentials"
                )
                os.environ["AWS_WEB_IDENTITY_TOKEN_FILE"] = str(
                    root / "web-identity",
                )
                os.environ["AWS_CONFIG_FILE"] = str(root / "aws-config")
                os.environ["AWS_SHARED_CREDENTIALS_FILE"] = str(
                    root / "aws-credentials",
                )
                os.environ["NODE_EXTRA_CA_CERTS"] = str(root / "corporate-ca.pem")
                os.environ["SSL_CERT_FILE"] = str(root / "tls-ca.pem")
                os.environ["SSL_CERT_DIR"] = str(root / "tls-ca")
                os.environ["SNOWFLAKE_ACCOUNT"] = "test-account"
                os.environ["SNOWFLAKE_CORTEX_TOKEN"] = "test-token-placeholder"
                os.environ["AZURE_RESOURCE_NAME"] = "test-resource"
                os.environ["ANTHROPIC_OAUTH_TOKEN"] = "test-token-placeholder"
                os.environ["AWS_BEDROCK_FORCE_HTTP1"] = "1"
                os.environ["AWS_BEDROCK_SKIP_AUTH"] = "1"
                os.environ["AZURE_CLIENT_ID"] = "test-client"
                os.environ["AZURE_CLIENT_SECRET"] = "test-token-placeholder"
                os.environ["AZURE_TENANT_ID"] = "test-tenant"
                os.environ["GCLOUD_PROJECT"] = "test-project"
                os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
                os.environ["CODEX_API_KEY"] = "test-token-placeholder"
                os.environ["CODEX_CA_CERTIFICATE"] = str(root / "codex-ca.pem")
                os.environ["COPILOT_GITHUB_TOKEN"] = "test-token-placeholder"
                os.environ["PI_OFFLINE"] = "1"
                os.environ["PI_SKIP_VERSION_CHECK"] = "1"
                os.environ["PI_TELEMETRY"] = "0"
                os.environ["NPM_TOKEN"] = "test-token-placeholder"
                os.environ["SENTRY_API_KEY"] = "test-token-placeholder"
                os.environ["SENTRY_AUTH_TOKEN"] = "test-token-placeholder"
                os.environ["DIGITALOCEAN_ACCESS_TOKEN"] = "test-token-placeholder"
                os.environ["GITLAB_TOKEN"] = "test-token-placeholder"
                os.environ["NODE_OPTIONS"] = "--require=/tmp/unsafe.js"
                os.environ["GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES"] = "1"
                os.environ["XDG_DATA_HOME"] = str(root / "opencode-auth")

                for engine in ("opencode", "pi"):
                    with self.subTest(engine=engine):
                        env = self.helper["safe_engine_env"](repo, engine=engine)
                        for key in (
                            "AWS_ROLE_ARN",
                            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
                            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                            "AWS_BEDROCK_FORCE_HTTP1",
                            "AWS_BEDROCK_SKIP_AUTH",
                            "AWS_CONFIG_FILE",
                            "AWS_SHARED_CREDENTIALS_FILE",
                            "AWS_WEB_IDENTITY_TOKEN_FILE",
                            "CEREBRAS_API_KEY",
                            "CLOUDFLARE_ACCOUNT_ID",
                            "CLOUDFLARE_API_TOKEN",
                            "COPILOT_GITHUB_TOKEN",
                            "DEEPSEEK_API_KEY",
                            "GOOGLE_APPLICATION_CREDENTIALS",
                            "NODE_EXTRA_CA_CERTS",
                            "SSL_CERT_DIR",
                            "SSL_CERT_FILE",
                            "SNOWFLAKE_ACCOUNT",
                            "SNOWFLAKE_CORTEX_TOKEN",
                            "AZURE_RESOURCE_NAME",
                            "ANTHROPIC_OAUTH_TOKEN",
                        ):
                            self.assertEqual(env[key], os.environ[key])
                        self.assertNotIn("NODE_OPTIONS", env)
                        self.assertNotIn("NPM_TOKEN", env)
                        self.assertNotIn("SENTRY_API_KEY", env)
                        self.assertNotIn("SENTRY_AUTH_TOKEN", env)
                        self.assertNotIn(
                            "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES",
                            env,
                        )
                        if engine == "opencode":
                            self.assertEqual(
                                env["DIGITALOCEAN_ACCESS_TOKEN"],
                                os.environ["DIGITALOCEAN_ACCESS_TOKEN"],
                            )
                            self.assertEqual(
                                env["GITLAB_TOKEN"],
                                os.environ["GITLAB_TOKEN"],
                            )
                            self.assertEqual(
                                env["XDG_DATA_HOME"],
                                str(root / "opencode-auth"),
                            )
                        else:
                            self.assertNotIn("DIGITALOCEAN_ACCESS_TOKEN", env)
                            self.assertNotIn("GITLAB_TOKEN", env)
                            self.assertEqual(env["PI_OFFLINE"], "1")
                            self.assertEqual(env["PI_SKIP_VERSION_CHECK"], "1")
                            self.assertEqual(env["PI_TELEMETRY"], "0")

                claude_env = self.helper["safe_engine_env"](repo, engine="claude")
                for key in (
                    "AZURE_CLIENT_ID",
                    "AZURE_CLIENT_SECRET",
                    "AZURE_TENANT_ID",
                    "GCLOUD_PROJECT",
                    "GOOGLE_CLOUD_PROJECT",
                    "AWS_ROLE_ARN",
                    "AWS_CONFIG_FILE",
                    "AWS_SHARED_CREDENTIALS_FILE",
                    "AWS_WEB_IDENTITY_TOKEN_FILE",
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "NODE_EXTRA_CA_CERTS",
                    "SSL_CERT_DIR",
                    "SSL_CERT_FILE",
                ):
                    self.assertEqual(claude_env[key], os.environ[key])
                self.assertNotIn("DEEPSEEK_API_KEY", claude_env)
                self.assertNotIn("NODE_OPTIONS", claude_env)
                codex_env = self.helper["safe_engine_env"](repo, engine="codex")
                for key in (
                    "CODEX_API_KEY",
                    "CODEX_CA_CERTIFICATE",
                    "SSL_CERT_DIR",
                    "SSL_CERT_FILE",
                ):
                    self.assertEqual(codex_env[key], os.environ[key])
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_multi_provider_custom_credentials_require_explicit_safe_names(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            try:
                os.environ["CORP_LLM_API_KEY"] = "test-token-placeholder"
                os.environ["CORP_AUTH_TOKEN"] = "test-token-placeholder"
                os.environ["AUTOREVIEW_PROVIDER_ENV_ALLOW"] = (
                    "CORP_LLM_API_KEY,CORP_AUTH_TOKEN"
                )

                for engine in ("opencode", "pi"):
                    env = self.helper["safe_engine_env"](repo, engine=engine)
                    self.assertEqual(
                        env["CORP_LLM_API_KEY"],
                        os.environ["CORP_LLM_API_KEY"],
                    )
                    self.assertEqual(
                        env["CORP_AUTH_TOKEN"],
                        os.environ["CORP_AUTH_TOKEN"],
                    )
                    self.assertNotIn("AUTOREVIEW_PROVIDER_ENV_ALLOW", env)

                os.environ["AUTOREVIEW_PROVIDER_ENV_ALLOW"] = "NODE_OPTIONS"
                with self.assertRaisesRegex(
                    SystemExit,
                    "invalid AUTOREVIEW_PROVIDER_ENV_ALLOW entry",
                ):
                    self.helper["safe_engine_env"](repo, engine="pi")
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_provider_credential_paths_are_forwarded_as_absolute(self) -> None:
        old_env = os.environ.copy()
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            try:
                os.chdir(repo)
                os.environ["AWS_CONFIG_FILE"] = "../shared/aws-config"
                os.environ["SSL_CERT_DIR"] = os.pathsep.join(
                    ("../tls/one", "../tls/two"),
                )

                env = self.helper["safe_engine_env"](repo, engine="pi")

                self.assertEqual(
                    env["AWS_CONFIG_FILE"],
                    str((root / "shared" / "aws-config").resolve()),
                )
                self.assertEqual(
                    env["SSL_CERT_DIR"],
                    os.pathsep.join(
                        (
                            str((root / "tls" / "one").resolve()),
                            str((root / "tls" / "two").resolve()),
                        )
                    ),
                )
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_env)

    def test_opencode_rejects_repo_local_xdg_auth_store(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            try:
                os.environ["XDG_DATA_HOME"] = str(repo / ".opencode-data")
                os.environ["AWS_CONFIG_FILE"] = str(repo / ".aws-config")
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(
                    repo / "provider-credentials.json"
                )
                os.environ["NODE_EXTRA_CA_CERTS"] = str(repo / "ca.pem")
                os.environ["SSL_CERT_FILE"] = str(repo / "tls-ca.pem")
                os.environ["SSL_CERT_DIR"] = os.pathsep.join(
                    (str(repo.parent / "tls-ca"), str(repo / "tls-ca")),
                )
                env = self.helper["safe_engine_env"](repo, engine="opencode")
                self.assertNotIn("XDG_DATA_HOME", env)
                self.assertNotIn("AWS_CONFIG_FILE", env)
                self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
                self.assertNotIn("NODE_EXTRA_CA_CERTS", env)
                self.assertNotIn("SSL_CERT_FILE", env)
                self.assertNotIn("SSL_CERT_DIR", env)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_engines_reject_repo_local_config_roots(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = str(repo / ".claude")
                os.environ["CODEX_HOME"] = str(repo / ".codex")
                os.environ["PI_CODING_AGENT_DIR"] = str(repo / ".pi")
                os.environ["CODEX_CA_CERTIFICATE"] = str(repo / "codex-ca.pem")
                os.environ["SSL_CERT_FILE"] = str(repo / "tls-ca.pem")
                os.environ["HOME"] = str(repo)
                os.environ["USERPROFILE"] = str(repo)
                claude_env = self.helper["safe_engine_env"](repo, engine="claude")
                codex_env = self.helper["safe_engine_env"](repo, engine="codex")
                pi_env = self.helper["safe_engine_env"](repo, engine="pi")
                self.assertNotIn("CLAUDE_CONFIG_DIR", claude_env)
                self.assertNotIn("CODEX_HOME", codex_env)
                self.assertNotIn("CODEX_CA_CERTIFICATE", codex_env)
                self.assertNotIn("SSL_CERT_FILE", codex_env)
                self.assertNotIn("PI_CODING_AGENT_DIR", pi_env)
                self.assertNotIn("HOME", claude_env)
                self.assertNotIn("USERPROFILE", claude_env)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_codex_auth_config_ignores_repo_local_home(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            config_dir = repo / ".codex"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                'forced_login_method = "api"\n',
                encoding="utf-8",
            )
            try:
                os.environ["CODEX_HOME"] = str(config_dir)
                self.assertEqual(self.helper["codex_auth_config_flags"](repo), [])
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_codex_runtime_home_links_only_auth_and_persists_refresh(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source_home = root / "host-home" / ".codex"
            runtime_home = root / "runtime" / "codex-home"
            source_home.mkdir(parents=True)
            source_auth = source_home / "auth.json"
            source_auth.write_text(
                '{"token":"test-token-placeholder"}',
                encoding="utf-8",
            )
            (source_home / "config.toml").write_text(
                'cli_auth_credentials_store = "file"\n',
                encoding="utf-8",
            )
            try:
                os.environ["CODEX_HOME"] = str(source_home)
                linked = self.helper["prepare_codex_runtime_auth"](repo, runtime_home)
                self.assertTrue(linked)
                self.assertTrue((runtime_home / "auth.json").is_file())
                self.assertTrue(
                    os.path.samefile(source_auth, runtime_home / "auth.json")
                )
                self.assertFalse((runtime_home / "config.toml").exists())
                self.assertIn(
                    'cli_auth_credentials_store="file"',
                    self.helper["codex_auth_config_flags"](
                        repo,
                        force_file=True,
                    ),
                )

                (runtime_home / "auth.json").write_text(
                    '{"token":"test-auth-token"}',
                    encoding="utf-8",
                )
                self.assertEqual(
                    json.loads(source_auth.read_text(encoding="utf-8"))["token"],
                    "test-auth-token",
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_codex_runtime_home_does_not_promote_keyring_fallback_file(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source_home = root / "host-home" / ".codex"
            source_home.mkdir(parents=True)
            (source_home / "auth.json").write_text(
                '{"token":"test-token-placeholder"}',
                encoding="utf-8",
            )
            (source_home / "config.toml").write_text(
                'cli_auth_credentials_store = "keyring"\n',
                encoding="utf-8",
            )
            try:
                os.environ["CODEX_HOME"] = str(source_home)
                self.assertFalse(
                    self.helper["prepare_codex_runtime_auth"](
                        repo,
                        root / "runtime" / "codex-home",
                    )
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_codex_runtime_home_fails_closed_when_linking_is_unavailable(
        self,
    ) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source_home = root / "host-home" / ".codex"
            source_home.mkdir(parents=True)
            source_auth = source_home / "auth.json"
            source_auth.write_text(
                '{"token":"test-token-placeholder"}',
                encoding="utf-8",
            )
            try:
                os.environ["CODEX_HOME"] = str(source_home)
                with (
                    mock.patch("os.link", side_effect=OSError("blocked")),
                    mock.patch.object(
                        Path,
                        "symlink_to",
                        side_effect=OSError("blocked"),
                    ),
                    self.assertRaisesRegex(
                        SystemExit,
                        "unable to isolate Codex file authentication",
                    ),
                ):
                    self.helper["prepare_codex_runtime_auth"](
                        repo,
                        root / "runtime" / "codex-home",
                    )
                self.assertEqual(
                    json.loads(source_auth.read_text(encoding="utf-8"))["token"],
                    "test-token-placeholder",
                )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_codex_runtime_home_preserves_auto_keyring_namespace(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            source_home = root / "host-home" / ".codex"
            runtime_home = root / "runtime" / "codex-home"
            source_home.mkdir(parents=True)
            (source_home / "auth.json").write_text(
                '{"token":"test-token-placeholder"}',
                encoding="utf-8",
            )
            (source_home / "config.toml").write_text(
                'cli_auth_credentials_store = "auto"\n',
                encoding="utf-8",
            )
            try:
                os.environ["CODEX_HOME"] = str(source_home)
                linked = self.helper["prepare_codex_runtime_auth"](
                    repo,
                    runtime_home,
                )
                self.assertFalse(linked)
                flags = self.helper["codex_auth_config_flags"](repo)
                self.assertIn('cli_auth_credentials_store="auto"', flags)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_empty_codex_home_uses_external_default(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            default_home = root / "host-home" / ".codex"
            default_home.mkdir(parents=True)
            try:
                os.environ["CODEX_HOME"] = ""
                with mock.patch.object(
                    Path,
                    "home",
                    return_value=default_home.parent,
                ):
                    self.assertEqual(
                        self.helper["codex_source_home"](repo),
                        default_home.resolve(),
                    )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_empty_codex_home_ignores_missing_default(self) -> None:
        old = os.environ.copy()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            missing_home = root / "missing-home"
            try:
                os.environ["CODEX_HOME"] = ""
                with mock.patch.object(
                    Path,
                    "home",
                    return_value=missing_home,
                ):
                    self.assertIsNone(
                        self.helper["codex_source_home"](repo)
                    )
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_opencode_web_search_preserves_explicit_exa_opt_in(self) -> None:
        old = os.environ.copy()
        try:
            os.environ["OPENCODE_ENABLE_EXA"] = "1"
            enabled = self.helper["opencode_review_env"](True)
            disabled = self.helper["opencode_review_env"](False)
            self.assertEqual(enabled["OPENCODE_ENABLE_EXA"], "1")
            self.assertNotIn("OPENCODE_ENABLE_EXA", disabled)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_codex_isolation_restricts_tool_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            runtime_root = root / "runtime"
            flags = self.helper["codex_config_isolation_flags"](
                repo,
                runtime_root,
            )

        for required in (
            f"sqlite_home={json.dumps(str((runtime_root / 'state').resolve()))}",
            f"log_dir={json.dumps(str((runtime_root / 'log').resolve()))}",
            "features.shell_snapshot=false",
            "features.hooks=false",
            "features.plugins=false",
            "skills.include_instructions=false",
            "skills.config=[]",
            'shell_environment_policy.inherit="core"',
            "shell_environment_policy.ignore_default_excludes=false",
            "shell_environment_policy.experimental_use_profile=false",
            "allow_login_shell=false",
            'default_permissions="autoreview"',
            'permissions.autoreview.filesystem={":minimal"="read",":workspace_roots"="read"}',
        ):
            self.assertIn(required, flags)
        set_flag = next(
            flag for flag in flags if flag.startswith("shell_environment_policy.set=")
        )
        for key, value in self.helper["codex_tool_git_env"]().items():
            self.assertIn(f"{key}={json.dumps(value)}", set_flag)

    def test_safe_engine_env_excludes_repo_local_path_entries(self) -> None:
        old_path = os.environ.get("PATH", "")
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            os.environ["PATH"] = f"{repo}{os.pathsep}{old_path}"
            try:
                env = self.helper["safe_engine_env"](repo, engine="codex")
            finally:
                os.environ["PATH"] = old_path

            self.assertNotIn(str(repo.resolve()), env["PATH"].split(os.pathsep))

    def test_find_command_rejects_explicit_repo_local_executables(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            (repo / "tools").mkdir()
            (root / "trusted").mkdir()
            repo_bin = self.helper["write_executable"](
                repo / "tools" / "codex",
                "#!/bin/sh\nexit 0\n",
            )
            external_bin = self.helper["write_executable"](
                root / "trusted" / "codex",
                "#!/bin/sh\nexit 0\n",
            )

            self.assertIsNone(
                self.helper["find_command"]("tools/codex", repo),
            )
            self.assertIsNone(
                self.helper["find_command"](str(repo_bin), repo),
            )
            self.assertEqual(
                self.helper["find_command"](str(external_bin), repo),
                str(Path(os.path.abspath(external_bin))),
            )
            self.assertEqual(
                self.helper["find_command"]("../trusted/codex", repo),
                str(Path(os.path.abspath(external_bin))),
            )

            external_link = root / "trusted" / "external-codex"
            repo_link = repo / "tools" / "external-codex"
            try:
                external_link.symlink_to(repo_bin)
                repo_link.symlink_to(external_bin)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    return
                raise
            self.assertIsNone(
                self.helper["find_command"](str(external_link), repo),
            )
            self.assertIsNone(
                self.helper["find_command"](str(repo_link), repo),
            )

    def test_validate_report_normalizes_relative_finding_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            report = {
                "findings": [
                    {
                        "title": "Finding",
                        "body": "Body",
                        "priority": "P1",
                        "confidence": 0.9,
                        "category": "bug",
                        "code_location": {"file_path": r".\src\index.ts", "line": 1},
                    }
                ],
                "overall_correctness": "patch is incorrect",
                "overall_explanation": "Explanation",
                "overall_confidence": 0.9,
            }

            self.helper["validate_report"](report, repo, {"src/index.ts"}, [])

            self.assertEqual(report["findings"][0]["code_location"]["file_path"], "src/index.ts")

            report["findings"][0]["code_location"]["file_path"] = r"src\index.ts"
            self.helper["validate_report"](report, repo, {r"src\index.ts"}, [])
            self.assertEqual(
                report["findings"][0]["code_location"]["file_path"],
                r"src\index.ts",
            )

            report["findings"][0]["code_location"]["file_path"] = " "
            with self.assertRaisesRegex(SystemExit, "invalid location"):
                self.helper["validate_report"](report, repo, {"src/index.ts"}, [])

            for invalid_path in (123, None, True):
                with self.subTest(invalid_path=invalid_path):
                    report["findings"][0]["code_location"] = {
                        "file_path": invalid_path,
                        "line": 1,
                    }
                    with self.assertRaisesRegex(SystemExit, "invalid location"):
                        self.helper["validate_report"](
                            report,
                            repo,
                            {"src/index.ts"},
                            [],
                        )

            report["findings"][0]["code_location"] = {
                "file_path": "src/index.ts",
                "line": True,
            }
            with self.assertRaisesRegex(SystemExit, "invalid location"):
                self.helper["validate_report"](report, repo, {"src/index.ts"}, [])

            report["findings"][0]["code_location"] = {
                "file_path": "src/index.ts",
                "line": 1,
                "extra": "ignored",
            }
            with self.assertRaisesRegex(
                SystemExit,
                "invalid code_location keys",
            ):
                self.helper["validate_report"](report, repo, {"src/index.ts"}, [])

    def test_print_report_escapes_terminal_controls(self) -> None:
        report = {
            "findings": [
                {
                    "title": "clear\x1b[2Jscreen",
                    "body": "first line\nsecond\u202eline café\udc9b",
                    "priority": "P1",
                    "confidence": 0.9,
                    "category": "security",
                    "code_location": {
                        "file_path": "src/\x9b2Jfile.py",
                        "line": 1,
                    },
                }
            ],
            "overall_correctness": "patch is incorrect",
            "overall_explanation": "explanation\x07",
            "overall_confidence": 0.9,
        }
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.helper["print_report"](report, label="review\x00label")

        rendered = output.getvalue()
        for control in (
            "\x00",
            "\x07",
            "\x1b",
            "\x9b",
            "\u202e",
            "\udc9b",
        ):
            self.assertNotIn(control, rendered)
        for escaped in (
            r"review\x00label",
            r"clear\x1b[2Jscreen",
            r"src/\x9b2Jfile.py",
            r"second\u202eline café\udc9b",
            r"explanation\x07",
        ):
            self.assertIn(escaped, rendered)
        self.assertIn("first line\nsecond", rendered)

    def test_validate_report_escapes_controls_in_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            report = {
                "findings": [
                    {
                        "title": "Finding",
                        "body": "Body",
                        "priority": "P1\x1b]52;c;VEVTVA==\x07",
                        "confidence": 0.9,
                        "category": "security",
                        "code_location": {
                            "file_path": "src/index.py",
                            "line": 1,
                        },
                    }
                ],
                "overall_correctness": "patch is incorrect",
                "overall_explanation": "Explanation",
                "overall_confidence": 0.9,
            }

            with self.assertRaises(SystemExit) as raised:
                self.helper["validate_report"](
                    report,
                    repo,
                    {"src/index.py"},
                    [],
                )

        message = str(raised.exception)
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\x07", message)
        self.assertIn(r"P1\x1b]52;c;VEVTVA==\x07", message)

    def test_safe_engine_env_ignores_inaccessible_path_entries(self) -> None:
        old_path = os.environ.get("PATH", "")
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            repo = init_repo(root)
            blocked = root / "blocked"
            os.environ["PATH"] = f"{blocked}{os.pathsep}{old_path}"
            original_exists = Path.exists

            def fake_exists(path: Path) -> bool:
                if str(path) == str(blocked):
                    raise PermissionError("access denied")
                return original_exists(path)

            try:
                with mock.patch.object(Path, "exists", fake_exists):
                    env = self.helper["safe_engine_env"](repo, engine="codex")
            finally:
                os.environ["PATH"] = old_path

            self.assertNotIn(str(blocked), env["PATH"].split(os.pathsep))

    def test_run_with_heartbeat_replaces_undecodable_engine_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            result = self.helper["run_with_heartbeat"](
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'\\x90\\n')",
                ],
                Path(tempdir),
                label="decode-test",
                heartbeat_seconds=1,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\ufffd", result.stdout)

    def test_large_repo_relative_evidence_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            evidence = repo / "evidence.txt"
            evidence.write_text("x" * 600_000, encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "file too large to scan safely"):
                self.helper["validate_evidence_file"](
                    repo,
                    "evidence.txt",
                    "--dataset",
                )

    def test_copilot_fails_closed_without_repo_only_read_sandbox(self) -> None:
        args = argparse.Namespace(
            copilot_bin="copilot",
            thinking=None,
            tools=True,
            model=None,
            web_search=False,
            stream_engine_output=False,
        )

        with tempfile.TemporaryDirectory() as tempdir:
            repo = init_repo(Path(tempdir))
            with self.assertRaisesRegex(
                SystemExit,
                r"ignored repository secrets; use codex, claude, or pi",
            ) as error:
                self.helper["run_copilot"](
                    args,
                    repo,
                    "Repository root: .\n\nprompt",
                )
            self.assertNotIn("opencode", str(error.exception))

    def test_claude_inventory_is_bundle_and_web_only(self) -> None:
        args = argparse.Namespace(
            claude_allowed_tools="WebFetch(domain:docs.example.com),WebSearch",
            web_search=True,
        )

        self.assertEqual(
            self.helper["claude_allowed_tools"](args),
            "WebFetch(domain:docs.example.com),WebSearch",
        )
        self.assertEqual(
            self.helper["claude_tool_inventory"](args),
            "WebFetch,WebSearch",
        )

        args.web_search = False
        self.assertEqual(
            self.helper["claude_allowed_tools"](args),
            "",
        )

        args.claude_allowed_tools = "Read"
        with self.assertRaisesRegex(SystemExit, "not read-only"):
            self.helper["claude_tool_inventory"](args)

        args.web_search = True
        args.claude_allowed_tools = "WebFetch"
        with self.assertRaisesRegex(SystemExit, "one explicit domain"):
            self.helper["claude_tool_inventory"](args)

    def test_stream_displays_escape_terminal_controls(self) -> None:
        control = chr(27) + "]52;c;VEVTVA==" + chr(7)
        codex = self.helper["CodexStreamDisplay"]()
        claude = self.helper["ClaudeStreamDisplay"]()
        codex_message = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": control,
                },
            }
        )

        for displayed in (
            codex("stdout", codex_message + "\n"),
            codex("stderr", control + "\n"),
            claude("stderr", control + "\n"),
        ):
            self.assertIsNotNone(displayed)
            assert displayed is not None
            self.assertNotIn(chr(27), displayed)
            self.assertNotIn(chr(7), displayed)
            self.assertIn(r"\x1b", displayed)
            self.assertIn(r"\x07", displayed)
            self.assertTrue(displayed.endswith("\n"))

    def test_run_with_stream_escapes_terminal_output_only(self) -> None:
        control = chr(27) + "]52;c;VEVTVA==" + chr(7)
        script = (
            "import sys;"
            "value=chr(27)+']52;c;VEVTVA=='+chr(7);"
            "sys.stdout.write(value+'\\n');"
            "sys.stderr.write(value+'\\n')"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = self.helper["run_with_stream"](
                [sys.executable, "-c", script],
                Path.cwd(),
                input_text=None,
                label="stream-test",
                heartbeat_seconds=60,
                stream_display=None,
                resolve_root=Path.cwd(),
            )

        self.assertIn(control, result.stdout)
        self.assertIn(control, result.stderr)
        for displayed in (stdout.getvalue(), stderr.getvalue()):
            self.assertNotIn(chr(27), displayed)
            self.assertNotIn(chr(7), displayed)
            self.assertIn(r"\x1b", displayed)
            self.assertIn(r"\x07", displayed)
            self.assertTrue(displayed.endswith("\n"))

    def test_self_test_shortcut_runs_deterministic_checks(self) -> None:
        command = [str(SCRIPT), "--self-test"]
        if os.name == "nt":
            command = [sys.executable, str(SCRIPT), "--self-test"]
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("autoreview engine isolation self-test: ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
