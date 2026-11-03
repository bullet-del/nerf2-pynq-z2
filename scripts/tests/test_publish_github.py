"""Publication safety and independent local Git round-trip tests; no network."""
import importlib.util
import json
import shutil
import io
import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("publisher", Path(__file__).resolve().parents[1] / "publish_github.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class PublicationSafety(unittest.TestCase):
    def info(self, empty=False, visibility="PUBLIC"):
        return {"nameWithOwner": "alice/nerf2-pynq-z2", "isEmpty": empty, "visibility": visibility}

    def test_rejects_shell_and_url_names(self):
        for name in ["--help", "a/b", "a;exit", "https://github.com/a/b", "repo.git"]:
            with self.assertRaises(P.PublishError):
                P.validate_name(name)

    def test_accepts_simple_name(self):
        self.assertEqual(P.validate_name("nerf2-pynq-z2"), "nerf2-pynq-z2")

    def test_rejects_credentials_and_local_origins(self):
        for url in ["https://token@github.com/alice/repo", "E:/old/repo", "https://example.com/a/b", "-x"]:
            with self.assertRaises(P.PublishError):
                P.remote_identity(url)

    def test_accepts_github_https_identity(self):
        self.assertEqual(P.remote_identity("https://github.com/Alice/repo.git"), ("alice", "repo"))

    def test_refuses_existing_nonempty_repository(self):
        with self.assertRaisesRegex(P.PublishError, "non-empty"):
            P.remote_action(self.info(), "", "alice", "nerf2-pynq-z2", "public")

    def test_can_attach_only_empty_unbound_repository(self):
        self.assertEqual(P.remote_action(self.info(empty=True), "", "alice", "nerf2-pynq-z2", "public"), "attach-empty")

    def test_resume_keeps_matching_origin(self):
        origin = "https://github.com/alice/nerf2-pynq-z2.git"
        self.assertEqual(P.remote_action(self.info(), origin, "alice", "nerf2-pynq-z2", "public"), "push")

    def test_never_changes_existing_visibility(self):
        with self.assertRaisesRegex(P.PublishError, "visibility"):
            P.remote_action(self.info(visibility="PRIVATE"), "", "alice", "nerf2-pynq-z2", "public")

    def test_never_repoints_foreign_origin(self):
        with self.assertRaisesRegex(P.PublishError, "elsewhere"):
            P.remote_action(self.info(), "https://github.com/bob/other.git", "alice", "nerf2-pynq-z2", "public")

    def test_missing_bound_repository_is_not_recreated(self):
        with self.assertRaisesRegex(P.PublishError, "lookup"):
            P.remote_action(None, "https://github.com/alice/nerf2-pynq-z2.git", "alice", "nerf2-pynq-z2", "public")

    def test_local_only_mode_never_bootstraps_or_authenticates(self):
        with mock.patch.object(P, "local_verification", return_value={"README.md"}), \
             mock.patch.object(P, "ensure_gh") as cli, mock.patch.object(P, "publish") as publish:
            P.main(["--check"])
        cli.assert_not_called()
        publish.assert_not_called()

    def test_visibility_must_be_explicit_for_upload(self):
        with self.assertRaises(SystemExit), mock.patch.object(P, "local_verification") as verify, \
             contextlib.redirect_stderr(io.StringIO()):
            P.main([])
        verify.assert_not_called()

    @unittest.skipUnless(shutil.which("git"), "Git required")
    def test_real_local_git_ignores_generated_files_and_commit_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("source\n", encoding="utf-8")
            (root / ".gitignore").write_text("/work/\n", encoding="utf-8")
            (root / "work").mkdir()
            (root / "work/token-like-test-file.txt").write_text("not-source", encoding="utf-8")
            user = {"login": "test-account", "id": 1, "name": "Local Test"}
            with mock.patch.object(P, "ROOT", root):
                first = P.init_source_git({"README.md", ".gitignore"}, user)
                second = P.init_source_git({"README.md", ".gitignore"}, user)
                self.assertEqual(first, second)
                tracked = P.run(["git", "ls-files"]).stdout.splitlines()
                self.assertEqual(set(tracked), {"README.md", ".gitignore"})

    @unittest.skipUnless(shutil.which("git"), "Git required")
    def test_publication_and_retry_push_real_git_to_isolated_local_remote(self):
        # GH account operations are simulated; Git commits, push and remote
        # SHA verification run for real against an isolated local bare repo.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"
            root.mkdir()
            (root / "README.md").write_text("source\n", encoding="utf-8")
            (root / ".gitignore").write_text("/work/\n", encoding="utf-8")
            bare = base / "remote.git"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            subprocess.run(["git", "init", "--initial-branch=main", str(root)], check=True, capture_output=True)
            url = "https://github.com/test-account/nerf2-pynq-z2.git"
            subprocess.run(["git", "-C", str(root), "config", "--local", "url." + bare.as_uri() + ".insteadOf", url], check=True)
            state = {"exists": False}
            original_run = P.run

            def fake_gh(args, capture=True, check=True, cwd=None):
                if str(args[0]) != "fake-gh":
                    return original_run(args, capture=capture, check=check, cwd=cwd)
                if args[1:3] == ["auth", "status"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[1:3] == ["api", "user"]:
                    return subprocess.CompletedProcess(args, 0, json.dumps({"login": "test-account", "id": 1}), "")
                if args[1:3] == ["repo", "view"]:
                    if state["exists"]:
                        data = {"nameWithOwner": "test-account/nerf2-pynq-z2", "isEmpty": False, "visibility": "PUBLIC"}
                        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
                    return subprocess.CompletedProcess(args, 1, "", "not found")
                if args[1:3] == ["api", "repos/test-account/nerf2-pynq-z2"]:
                    return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
                if args[1:3] == ["repo", "create"]:
                    state["exists"] = True
                    original_run(["git", "remote", "add", "origin", url])
                    return subprocess.CompletedProcess(args, 0, "", "")
                raise AssertionError("Unexpected external account command: " + repr(args))

            with mock.patch.object(P, "ROOT", root), mock.patch.object(P, "run", side_effect=fake_gh):
                P.publish("fake-gh", {"README.md", ".gitignore"}, "nerf2-pynq-z2", "public")
                first = json.loads((root / "work/reports/github-publication.json").read_text(encoding="utf-8"))
                P.publish("fake-gh", {"README.md", ".gitignore"}, "nerf2-pynq-z2", "public")
                second = json.loads((root / "work/reports/github-publication.json").read_text(encoding="utf-8"))
            self.assertEqual(first["commit"], second["commit"])
            self.assertEqual(second["status"], "PUBLISHED")
            remote_commit = subprocess.check_output(["git", "--git-dir", str(bare), "rev-parse", "refs/heads/main"], text=True).strip()
            self.assertEqual(remote_commit, second["commit"])


if __name__ == "__main__":
    unittest.main()
