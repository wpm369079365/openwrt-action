from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zerowrt_core import ota, util

INFO = {
    "dist": "ZeroWrt",
    "version": "20261102",
    "build_date": 1793600000,
    "board": "mediatek",
    "subtarget": "filogic",
    "target": "mediatek/filogic",
    "device": "ruijie_rg-x60-new-mtkuboot",
    "dt_model": "ruijie,rg-x60-new-mtkuboot",
    "profile": "ruijie_rg-x60-new",
    "artifact": "ruijie_rg-x60-new",
    "device_vendor": "Ruijie",
    "device_model": "RG-X60 New",
    "source_repo": "chasey-dev/immortalwrt-mt798x-rebase",
    "source_commit": "abcdef1234567890",
}


class OtaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        self.image = self.dist / "immortalwrt-mediatek-filogic-ruijie_rg-x60-new-mtkuboot-squashfs-sysupgrade.bin"
        self.image.write_bytes(b"firmware-image")
        digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        (self.dist / "SHA256SUMS").write_text(f"{digest}  {self.image.name}\n", encoding="utf-8")
        (self.dist / "BUILD_INFO.txt").write_text(
            "ZeroWrt firmware\nbuilt: 2026-11-02 10:00:00 UTC\n"
            "tree: mediatek/filogic\ndevice: ruijie_rg-x60-new-mtkuboot\ntarget: mediatek/filogic\n",
            encoding="utf-8",
        )

    def test_select_image_skips_initramfs(self) -> None:
        (self.dist / "immortalwrt-mediatek-filogic-ruijie_rg-x60-new-initramfs-recovery.itb").write_bytes(b"x")
        self.assertEqual(ota.select_image(self.dist), self.image)

    def test_generate_index_and_history(self) -> None:
        history = self.dist / "history.json"
        existing = self.dist / "ota.json"
        existing.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "devices": {"other-device": {"version": "1", "build_date": 1}},
                }
            ),
            encoding="utf-8",
        )
        index = ota.generate_index(
            self.dist,
            info=INFO,
            repo="MinimaxFlora/ZeroWrt",
            tag="ruijie_rg-x60-new",
            existing=existing,
            history=history,
        )
        self.assertEqual(index["schema"], 2)
        self.assertIn("other-device", index["devices"])
        self.assertIn("ruijie_rg-x60-new-mtkuboot", index["devices"])
        self.assertIn("ruijie,rg-x60-new-mtkuboot", index["devices"])
        entry = index["devices"]["ruijie_rg-x60-new-mtkuboot"]
        self.assertTrue(entry["url"].endswith(self.image.name))
        self.assertEqual(entry["sha256"], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(entry["build_date"], 1793600000)
        self.assertEqual(entry["label"], "Ruijie RG-X60 New")
        data = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual(len(data["releases"]), 1)
        ota.generate_index(
            self.dist,
            info=INFO,
            repo="MinimaxFlora/ZeroWrt",
            tag="ruijie_rg-x60-new",
            existing=existing,
            history=history,
        )
        data = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual(len(data["releases"]), 1)

    def test_no_image_raises(self) -> None:
        self.image.unlink()
        with self.assertRaises(util.BuildError):
            ota.generate_index(self.dist, info=INFO, repo="a/b", tag="c")

    def test_embed_metadata_and_uci_defaults(self) -> None:
        tree = Path(self.tmp.name) / "tree"
        (tree / "package" / "base-files" / "files").mkdir(parents=True)
        metadata = ota.embed(
            tree,
            info=INFO,
            api_url="https://example.com/ota.json",
            download_proxy="https://proxy.example.com",
        )
        self.assertEqual(metadata["device"], "ruijie_rg-x60-new-mtkuboot")
        self.assertEqual(metadata["model"], "ruijie,rg-x60-new-mtkuboot")
        build_json = json.loads(
            (tree / "package/base-files/files/etc/ota/build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(build_json["version"], "20261102")
        defaults = (tree / "package/base-files/files/etc/uci-defaults/90-luci-app-ota").read_text(
            encoding="utf-8"
        )
        self.assertIn("api_url='https://example.com/ota.json'", defaults)
        self.assertIn("download_proxy='https://proxy.example.com'", defaults)
        self.assertEqual((tree / "version.date").read_text(encoding="utf-8").strip(), "1793600000")

    def test_publish_no_push(self) -> None:
        site = Path(self.tmp.name) / "site"
        (site / "assets").mkdir(parents=True)
        (site / "index.html").write_text(
            '<a href="https://github.com/MinimaxFlora/ZeroWrt">ZeroWrt</a>',
            encoding="utf-8",
        )
        (site / "assets" / "app.js").write_text("repo: 'MinimaxFlora/ZeroWrt'", encoding="utf-8")
        (site / "CNAME").write_text("old.example.com\n", encoding="utf-8")
        out = Path(self.tmp.name) / "out"
        ota.publish(
            dist=self.dist,
            info=INFO,
            repo="MinimaxFlora/ZeroWrt",
            tag="ruijie_rg-x60-new",
            site=site,
            no_push=True,
            out=out,
        )
        index = json.loads((out / "ota.json").read_text(encoding="utf-8"))
        self.assertIn("ruijie_rg-x60-new-mtkuboot", index["devices"])
        self.assertFalse((out / "CNAME").exists())
        self.assertIn("MinimaxFlora/ZeroWrt", (out / "assets" / "app.js").read_text(encoding="utf-8"))
        self.assertTrue((out / "history.json").exists())
        self.assertTrue((out / ".nojekyll").exists())

    def test_shell_quote(self) -> None:
        self.assertEqual(ota._shell_quote("a'b"), "'a'\\''b'")

    def test_device_keys_are_deduplicated(self) -> None:
        keys = ota.device_keys(
            {"device": "d", "dt_model": "v,d", "profile": "p", "artifact": "p"}
        )
        self.assertEqual(keys, ["d", "v,d", "p"])


if __name__ == "__main__":
    unittest.main()
