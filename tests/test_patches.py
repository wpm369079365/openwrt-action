from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from zerowrt_core import patches, profiles, util

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class PatchApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "hello.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "init"], check=True)
        self.patch = Path(self.tmp.name) / "change.patch"
        self.patch.write_text(
            "diff --git a/hello.txt b/hello.txt\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1,2 +1,2 @@\n"
            " hello\n"
            "-world\n"
            "+ZeroWrt\n",
            encoding="utf-8",
        )

    def test_apply_is_idempotent(self) -> None:
        self.assertEqual(patches.apply_patch(self.repo, self.patch), "applied")
        self.assertEqual((self.repo / "hello.txt").read_text(), "hello\nZeroWrt\n")
        self.assertEqual(patches.apply_patch(self.repo, self.patch), "already")

    def test_bad_patch_raises(self) -> None:
        (self.repo / "hello.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(util.BuildError):
            patches.apply_patch(self.repo, self.patch)

    def test_refreshed_luci_patches_are_present(self) -> None:
        for name in patches.LUCI_PATCHES:
            path = SCRIPTS / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text(encoding="utf-8")
            self.assertIn("diff --git ", text)
            self.assertIn("index ", text)

    def test_0001_points_at_this_project(self) -> None:
        text = (SCRIPTS / patches.LUCI_PATCHES[0]).read_text(encoding="utf-8")
        self.assertIn("github.com/MinimaxFlora/ZeroWrt", text)
        self.assertNotIn("OpenWrt_MediaTek_Builder", text)


class PatchPlanTest(unittest.TestCase):
    def test_the_2410_tree_skips_gcc16_and_luci_patches(self) -> None:
        groups = patches.plan(profiles.require("ruijie_rg-x60-new-2410"))
        self.assertEqual(groups["luci"], [])
        self.assertEqual(groups["source"], [])
        self.assertFalse(groups["gcc16"])

    def test_no_profile_keeps_the_historical_behaviour(self) -> None:
        groups = patches.plan(None)
        self.assertEqual(groups["luci"], patches.LUCI_PATCHES)
        self.assertTrue(groups["gcc16"])


if __name__ == "__main__":
    unittest.main()
