from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zerowrt_core import cache, util


class CacheTest(unittest.TestCase):
    def test_asset_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "toolchain-aarch64_generic_gcc-16.1.0_musl").mkdir()
            self.assertEqual(
                cache.asset_name(staging),
                "toolchain_musl_aarch64_generic_gcc-16.tar.zst",
            )

    def test_asset_name_requires_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(util.BuildError):
            cache.asset_name(Path(tmp))

    def test_clean_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache.clean(Path(tmp))

    def test_expected_gcc_major(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / ".config").write_text('CONFIG_GCC_VERSION="16.1.0"\n', encoding="utf-8")
            self.assertEqual(cache._expected_gcc_major(tree), "16")
            (tree / ".config").write_text("CONFIG_GCC_VERSION_14=y\n", encoding="utf-8")
            self.assertEqual(cache._expected_gcc_major(tree), "14")


if __name__ == "__main__":
    unittest.main()
