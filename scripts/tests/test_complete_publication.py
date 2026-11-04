"""Actual local Git replacement and retry tests, without network access."""
import json
import subprocess
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
