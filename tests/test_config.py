from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from zerowrt_core import config, profiles, util


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = profiles.require("ruijie_rg-x60-new-2410-homeproxy")
        self.target = config.target_symbol(self.profile)

    def test_set_option_replaces_assignment(self) -> None:
        text = "CONFIG_PACKAGE_luci-app-ota=y\nCONFIG_OTHER=y\n"
        updated = config.set_option(text, "CONFIG_PACKAGE_luci-app-ota", "n")
        self.assertIn("CONFIG_PACKAGE_luci-app-ota=n", updated)
        self.assertNotIn("CONFIG_PACKAGE_luci-app-ota=y", updated)
        self.assertIn("CONFIG_OTHER=y", updated)

    def test_set_option_replaces_not_set(self) -> None:
        text = "# CONFIG_PACKAGE_luci-app-ota is not set\n"
        updated = config.set_option(text, "CONFIG_PACKAGE_luci-app-ota", "y")
        self.assertIn("CONFIG_PACKAGE_luci-app-ota=y", updated)
        self.assertNotIn("is not set", updated)

    def test_render_adds_ota_and_keeps_target(self) -> None:
        text = f"{self.target}=y\nCONFIG_PACKAGE_luci-app-ota=y\n"
        updated = config.render_config(text, self.profile)
        self.assertIn(f"{self.target}=y", updated)
        self.assertIn("CONFIG_PACKAGE_luci-i18n-ota-zh-cn=y", updated)
        self.assertEqual(updated.count("CONFIG_PACKAGE_luci-app-ota=y"), 1)

    def test_every_profile_ships_ota(self) -> None:
        for name in profiles.names():
            with self.subTest(profile=name):
                profile = profiles.require(name)
                self.assertTrue(profiles.source(profile).ota)
                target = config.target_symbol(profile)
                # The device configs never mention the app; it is added here.
                updated = config.render_config(f"{target}=y\n", profile)
                self.assertIn("CONFIG_PACKAGE_luci-app-ota=y", updated)
                self.assertIn("CONFIG_PACKAGE_luci-i18n-ota-zh-cn=y", updated)
                required = config.required_selections(profile)
                self.assertIn("CONFIG_PACKAGE_luci-app-ota=y", required)

    def test_render_skips_ota_for_a_tree_without_the_app(self) -> None:
        profile = profiles.require("ruijie_rg-x60-new-2410")
        plain = profiles.source(profile)
        # A plain image is still expressible: the flag is what stands the OTA
        # steps down when a source tree is built without the app.
        with mock.patch.dict(
            profiles.SOURCES, {plain.key: replace(plain, ota=False)}
        ):
            target = config.target_symbol(profile)
            updated = config.render_config(f"{target}=y\n", profile)
            self.assertIn(f"{target}=y", updated)
            self.assertNotIn("luci-app-ota", updated)
            self.assertNotIn("luci-i18n-ota", updated)
            required = config.required_selections(profile)
            self.assertFalse([item for item in required if "ota" in item])

    def test_render_rejects_wrong_device(self) -> None:
        with self.assertRaises(util.BuildError):
            config.render_config("CONFIG_TARGET_mediatek_filogic=y\n", self.profile)

    def test_required_selections_only_enforce_what_the_config_asks_for(self) -> None:
        profile = profiles.require("ruijie_rg-x60-new-2410-homeproxy")
        requested = (
            f"{config.target_symbol(profile)}=y\n"
            "CONFIG_PACKAGE_luci-app-homeproxy=y\n"
            "CONFIG_PACKAGE_sing-box=y\n"
        )
        required = config.required_selections(profile, requested)
        self.assertIn("CONFIG_PACKAGE_luci-app-homeproxy=y", required)
        self.assertIn("CONFIG_PACKAGE_sing-box=y", required)
        # Not selected by this config, so not something defconfig can drop.
        self.assertNotIn("CONFIG_PACKAGE_luci-theme-argon=y", required)
        self.assertFalse([item for item in required if "openclash" in item])

    def test_every_config_uses_homeproxy_without_naive_outbound(self) -> None:
        text = util.read_text(
            profiles.PROJECT_ROOT / "configs" / "ruijie-rg-x60-new-2410-homeproxy.config"
        )
        self.assertIn("CONFIG_PACKAGE_luci-app-homeproxy=y", text)
        self.assertIn("CONFIG_PACKAGE_sing-box=y", text)
        # libcronet is not in this tree; leaving the flag on drops sing-box,
        # and HomeProxy with it.
        self.assertIn("# CONFIG_SING_BOX_BUILD_NAIVE_OUTBOUND is not set", text)
        self.assertNotIn("CONFIG_PACKAGE_luci-app-openclash=y", text)

    def test_resolve_device_reads_image_makefile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            image = tree / "target" / "linux" / "mediatek" / "image"
            image.mkdir(parents=True)
            (image / "filogic-ext.mk").write_text(
                "define Device/ruijie_rg-x60-new\n"
                "  DEVICE_VENDOR := Ruijie\n"
                "  DEVICE_MODEL := RG-X60 New\n"
                "  DEVICE_VARIANT := (MTK U-Boot layout)\n"
                "  DEVICE_DTS := mt7986a-ruijie-rg-x60-new\n"
                "endef\n",
                encoding="utf-8",
            )
            info = config.resolve_device(tree, self.profile)
            self.assertEqual(info["device_vendor"], "Ruijie")
            self.assertEqual(info["device_model"], "RG-X60 New")
            self.assertEqual(info["device_dts"], "mt7986a-ruijie-rg-x60-new")


if __name__ == "__main__":
    unittest.main()
