"""Mock Git subprocesses: no network and no repository execution."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from github_service import clone_repository


class CloneSafetyTests(unittest.TestCase):
    def test_order_and_isolation(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if "clone" in args:
                root = Path(args[-1])
                (root / ".git").mkdir(parents=True)
            return SimpleNamespace(stdout=b"100644 blob abc\tsample.py\0")

        with patch("github_service.subprocess.run", side_effect=fake_run):
            owner, _ = clone_repository("https://github.com/example/project")
            owner.cleanup()
        self.assertEqual(len(calls), 3)
        self.assertIn("--no-checkout", calls[0][0])
        self.assertIn("ls-tree", calls[1][0])
        self.assertIn("checkout", calls[2][0])
        for args, kwargs in calls:
            self.assertEqual(kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertTrue(any(item.startswith("core.hooksPath=") for item in args))
            self.assertNotIn("shell", kwargs)

    def test_symlink_tree_rejected_before_checkout(self):
        calls = []
        locations = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if "clone" in args:
                root = Path(args[-1])
                locations.append(root.parent)
                (root / ".git").mkdir(parents=True)
            return SimpleNamespace(stdout=b"120000 blob abc\tescape.py\0")

        with patch("github_service.subprocess.run", side_effect=fake_run):
            with self.assertRaises(ValueError):
                clone_repository("https://github.com/example/project")
        self.assertFalse(any("checkout" in args for args in calls))
        self.assertFalse(locations[0].exists())

    def test_invalid_urls_never_launch_git(self):
        with patch("github_service.subprocess.run") as run:
            for url in ["file:///tmp/repo", "https://github.com@evil.test/a/b", "https://github.com/a/b?x=1", "https://github.com/../b"]:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    clone_repository(url)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
