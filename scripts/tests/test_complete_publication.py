"""Actual local Git replacement and retry tests, without network access."""
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_complete as publisher


class CompleteReplacement(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.checkout = self.base / "checkout"
        self.source = self.base / "source"
        self.checkout.mkdir()
        self.source.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Local verification")
        self.git("config", "user.email", "verification@example.invalid")
        (self.checkout / "old.txt").write_text("old tree", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "Original tree")
        self.before = self.git("rev-parse", "HEAD").strip()
        (self.source / "new.txt").write_text("complete tree", encoding="utf-8")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.checkout, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
        return result.stdout

    def test_replacement_deletes_old_files_preserves_history_and_retries(self):
        commit = publisher.replace_checkout(self.checkout, self.source, {"new.txt"})
        self.assertFalse((self.checkout / "old.txt").exists())
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD").strip(), "new.txt")
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), self.before)
        self.assertEqual(publisher.replace_checkout(self.checkout, self.source, {"new.txt"}), commit)
        bare = self.base / "remote.git"
        self.git("init", "--bare", str(bare))
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "origin", "main")
        self.assertIn(commit, self.git("ls-remote", "origin", "refs/heads/main"))

    def test_rejects_overlapping_source_before_deleting(self):
        with self.assertRaises(RuntimeError):
            publisher.replace_checkout(self.checkout, self.checkout, {"new.txt"})
        self.assertTrue((self.checkout / "old.txt").exists())

    def test_rejects_dirty_checkout_before_deleting(self):
        (self.checkout / "personal.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            publisher.replace_checkout(self.checkout, self.source, {"new.txt"})
        self.assertTrue((self.checkout / "old.txt").exists())

    def test_documentation_update_reuses_an_ancestor_artifact_release(self):
        commit = publisher.replace_checkout(self.checkout, self.source, {"new.txt"})
        result = publisher.release_source_commit({"draft": False}, [self.before], commit, self.checkout)
        self.assertEqual(result, self.before)

    def test_new_draft_can_precede_creation_of_its_git_tag(self):
        release = {"draft": True, "target_commitish": self.before}
        self.assertEqual(publisher.release_source_commit(release, [], self.before, self.checkout), self.before)

    def test_published_release_without_tag_is_rejected(self):
        with self.assertRaises(RuntimeError):
            publisher.release_source_commit({"draft": False}, [], self.before, self.checkout)

class FetchRetry(unittest.TestCase):
    def test_http_reset_retries_the_same_fetch(self):
        reset = subprocess.CompletedProcess([], 128, "", "curl 92 HTTP/2 stream reset\nfatal: early EOF")
        success = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(publisher, "run", side_effect=[reset, success]) as commands, \
                mock.patch.object(publisher.time, "sleep") as pause:
            publisher.fetch_main(Path("checkout"))
        self.assertEqual(commands.call_count, 2)
        self.assertEqual(commands.call_args_list[0], commands.call_args_list[1])
        pause.assert_called_once_with(2)

    def test_auth_failure_keeps_git_error_without_retry(self):
        failure = subprocess.CompletedProcess([], 128, "", "fatal: Authentication failed")
        with mock.patch.object(publisher, "run", return_value=failure) as commands, \
                mock.patch.object(publisher.time, "sleep") as pause:
            with self.assertRaisesRegex(RuntimeError, "Authentication failed"):
                publisher.fetch_main(Path("checkout"))
        commands.assert_called_once()
        pause.assert_not_called()


class DraftReleaseLookup(unittest.TestCase):
    repository = "bullet-del/nerf2-pynq-z2"
    tag = "stage39-complete-20261104"
    draft = {"id": 433665883, "tag_name": tag, "draft": True,
             "target_commitish": "a" * 40, "assets": []}

    def response(self, value=None, error=""):
        return subprocess.CompletedProcess([], 1 if error else 0,
                                           stdout=json.dumps(value), stderr=error)

    def test_actual_404_then_existing_draft_is_resumed(self):
        replies = [self.response(error="gh: Not Found (HTTP 404)"), self.response([self.draft])]
        with mock.patch.object(publisher, "run", side_effect=replies) as commands:
            found = publisher.ensure_release("gh", self.repository, self.tag, "b" * 40)
        self.assertEqual(found["id"], 433665883)
        self.assertEqual(commands.call_count, 2)
        self.assertIn("?per_page=100&page=1", commands.call_args_list[1].args[0][2])
        self.assertFalse(any("create" in call.args[0] for call in commands.call_args_list))

    def test_fresh_draft_is_reloaded_through_list_after_creation(self):
        with mock.patch.object(publisher, "find_release", side_effect=[None, self.draft]), \
                mock.patch.object(publisher, "run") as commands:
            found = publisher.ensure_release("gh", self.repository, self.tag, "a" * 40)
        self.assertEqual(found["id"], 433665883)
        self.assertEqual(commands.call_count, 1)
        self.assertEqual(commands.call_args.args[0][1:3], ["release", "create"])

    def test_published_release_uses_direct_tag_lookup(self):
        published = dict(self.draft, draft=False)
        with mock.patch.object(publisher, "run", return_value=self.response(published)) as commands:
            found = publisher.find_release("gh", self.repository, self.tag)
        self.assertFalse(found["draft"])
        self.assertEqual(commands.call_count, 1)

    def test_network_error_is_not_treated_as_missing_release(self):
        with mock.patch.object(publisher, "run", return_value=self.response(error="connection reset")) as commands:
            with self.assertRaises(RuntimeError):
                publisher.ensure_release("gh", self.repository, self.tag, "a" * 40)
        self.assertEqual(commands.call_count, 1)

    def test_lookup_paginates_before_deciding_release_is_missing(self):
        unrelated = [{"tag_name": "older-" + str(n)} for n in range(100)]
        replies = [self.response(error="HTTP 404"), self.response(unrelated), self.response([self.draft])]
        with mock.patch.object(publisher, "run", side_effect=replies) as commands:
            found = publisher.find_release("gh", self.repository, self.tag)
        self.assertEqual(found["id"], 433665883)
        self.assertIn("page=2", commands.call_args.args[0][2])

    def test_missing_release_returns_none_after_empty_list(self):
        replies = [self.response(error="HTTP 404"), self.response([])]
        with mock.patch.object(publisher, "run", side_effect=replies):
            self.assertIsNone(publisher.find_release("gh", self.repository, self.tag))


if __name__ == "__main__":
    unittest.main()
