from __future__ import annotations

import tarfile
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

from zerowrt_core import build, state, util


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        (self.tree / ".config").write_text("CONFIG_TARGET_mediatek=y\n", encoding="utf-8")
        target = self.tree / "bin" / "targets" / "mediatek" / "filogic"
        target.mkdir(parents=True)
        (target / "immortalwrt-mediatek-filogic-ruijie_rg-x60-new-mtkuboot-squashfs-sysupgrade.bin").write_bytes(
            b"firmware"
        )
        (target / "immortalwrt-mediatek-filogic-ruijie_rg-x60-new-mtkuboot.manifest").write_text(
            "kernel - 6.12.50\n", encoding="utf-8"
        )
        packages = self.tree / "bin" / "packages" / "aarch64_cortex-a53" / "base"
        packages.mkdir(parents=True)
        (packages / "foo-1.0.apk").write_bytes(b"apk")
        state.write(
            self.tree,
            {
                "board": "mediatek",
                "subtarget": "filogic",
                "target": "mediatek/filogic",
                "device": "ruijie_rg-x60-new-mtkuboot",
                "device_vendor": "Ruijie",
                "device_model": "RG-X60 New",
                "profile": "ruijie_rg-x60-new",
                "artifact": "ruijie_rg-x60-new",
                "version": "20261102",
                "build_date": int(time.time()),
                "source_repo": "chasey-dev/immortalwrt-mt798x-rebase",
                "source_ref": "25.12",
                "source_commit": "abc123",
                "layout": "MTK U-Boot layout (mtkuboot)",
            },
        )

    def test_collect_keeps_firmware_and_build_metadata(self) -> None:
        out = self.tree / "dist"
        build.collect(self.tree, out)
        names = {path.name for path in out.iterdir()}
        self.assertIn("immortalwrt-mediatek-filogic-ruijie_rg-x60-new-mtkuboot-squashfs-sysupgrade.bin", names)
        self.assertIn("build.config", names)
        self.assertIn("Packages.tar.gz", names)
        self.assertIn("BUILD_INFO.txt", names)
        self.assertIn("SHA256SUMS", names)
        info = (out / "BUILD_INFO.txt").read_text(encoding="utf-8")
        self.assertIn("kernel: 6.12.50", info)
        self.assertIn("source commit: abc123", info)
        checksums = (out / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertIn("immortalwrt-mediatek-filogic-ruijie_rg-x60-new-mtkuboot-squashfs-sysupgrade.bin", checksums)
        with tarfile.open(out / "Packages.tar.gz", "r:gz") as archive:
            self.assertTrue(any(name.endswith("foo-1.0.apk") for name in archive.getnames()))

    def test_missing_target_raises(self) -> None:
        (self.tree / "bin" / "targets").rename(self.tree / "bin" / "targets-moved")
        with self.assertRaises(util.BuildError):
            build.collect(self.tree, self.tree / "dist")

    def test_compile_jobs_is_positive(self) -> None:
        self.assertGreaterEqual(build.compile_jobs(4), 1)
        self.assertGreaterEqual(build.compile_jobs(1), 1)

    def test_state_helpers(self) -> None:
        data = state.read(self.tree)
        expected = datetime.fromtimestamp(state.build_date(data), UTC).strftime("%Y%m%d")
        self.assertEqual(state.version_for(state.build_date(data)), expected)
        self.assertIn("ruijie_rg-x60-new", state.fingerprint(data))


if __name__ == "__main__":
    unittest.main()
