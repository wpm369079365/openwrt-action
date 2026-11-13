from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zerowrt_core import packages, util


class PackagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        self._make("package/new/foo/Makefile", "PKG_NAME:=foo\n$(eval $(call BuildPackage,foo))\n")
        self._make(
            "package/new/v2ray-geodata/Makefile",
            "PKG_NAME:=v2ray-geodata\n"
            "$(eval $(call BuildPackage,v2ray-geoip))\n"
            "$(eval $(call BuildPackage,v2ray-geosite))\n",
        )
        self._make("feeds/foo/Makefile", "PKG_NAME:=foo\n")
        self._make("feeds/luci/applications/foo/Makefile", "PKG_NAME:=foo\n")
        self._make("package/feeds/luci/foo/Makefile", "PKG_NAME:=foo\n")
        self._make("feeds/unrelated/Makefile", "PKG_NAME:=unrelated\n")

    def _make(self, relative: str, text: str) -> None:
        path = self.tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_declared_names_follow_buildpackage(self) -> None:
        names = packages.declared_package_names(self.tree / "package/new/v2ray-geodata")
        self.assertEqual(names, {"v2ray-geodata", "v2ray-geoip", "v2ray-geosite"})

    def test_new_package_names(self) -> None:
        names = packages.new_package_names(self.tree)
        self.assertIn("foo", names)
        self.assertIn("v2ray-geosite", names)

    def test_prune_removes_feed_and_installed_copies(self) -> None:
        removed = packages.prune_feed_conflicts(self.tree)
        self.assertFalse((self.tree / "feeds/foo").exists())
        self.assertFalse((self.tree / "feeds/luci/applications/foo").exists())
        self.assertFalse((self.tree / "package/feeds/luci/foo").exists())
        self.assertTrue((self.tree / "feeds/unrelated/Makefile").exists())
        self.assertEqual(len(removed), 3)

    def test_prune_is_safe_without_package_new(self) -> None:
        other = self.tree / "other"
        other.mkdir()
        self.assertEqual(packages.prune_feed_conflicts(other), [])

    def test_shared_multi_package_name_is_pruned(self) -> None:
        self._make("feeds/packages/net/v2ray-geodata/Makefile", "PKG_NAME:=v2ray-geodata\n")
        packages.prune_feed_conflicts(self.tree)
        self.assertFalse((self.tree / "feeds/packages/net/v2ray-geodata").exists())

    def _write_layout(self) -> Path:
        """Create the package/new layout add_external_packages validates."""
        helloworld = self.tree / "package/new/helloworld"
        for name in ("luci-app-openclash", "luci-theme-argon", "luci-app-argon-config", "v2ray-geodata"):
            self._make(f"package/new/helloworld/{name}/Makefile", "PKG_NAME:=x\n")
        for name in ("openlist2", "luci-app-openlist2"):
            self._make(f"package/new/openlist/{name}/Makefile", "PKG_NAME:=x\n")
        return helloworld

    def test_layout_check_requires_the_ota_app_by_default(self) -> None:
        helloworld = self._write_layout()
        with self.assertRaises(util.BuildError):
            packages._validate_layout(self.tree / "package/new", helloworld)

    def test_layout_check_accepts_a_build_without_the_ota_app(self) -> None:
        helloworld = self._write_layout()
        packages._validate_layout(self.tree / "package/new", helloworld, with_ota=False)


if __name__ == "__main__":
    unittest.main()
