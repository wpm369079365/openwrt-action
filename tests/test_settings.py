from __future__ import annotations

import unittest

from zerowrt_core import profiles, settings, util


class SettingsTest(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        value = settings.BuildSettings.from_environment({})
        self.assertEqual(value.ip, "10.0.0.1")
        self.assertEqual(value.ssid("2g"), "ZeroWrt-2.4G")
        self.assertEqual(value.ssid("5g"), "ZeroWrt-5G")
        self.assertEqual(value.ssid("6g"), "ZeroWrt-6G")

    def test_environment_override(self) -> None:
        value = settings.BuildSettings.from_environment(
            {
                "ZEROWRT_IP": "192.168.8.1",
                "ZEROWRT_WIFI_NAME": "Lab Net",
                "ZEROWRT_WIFI_PASSWORD": "abcdefgh",
                "ZEROWRT_DEVICE": "ruijie_rg-x60-new",
                "ZEROWRT_DIST": "ZeroLab",
            }
        )
        self.assertEqual(value.ip, "192.168.8.1")
        self.assertEqual(value.ssid("5g"), "Lab Net-5G")

    def test_invalid_ip(self) -> None:
        with self.assertRaises(util.BuildError):
            settings.BuildSettings(ip="not-an-ip").validated()
        with self.assertRaises(util.BuildError):
            settings.BuildSettings(ip="2001:db8::1").validated()

    def test_short_password(self) -> None:
        with self.assertRaises(util.BuildError):
            settings.BuildSettings(root_password="short").validated()

    def test_wifi_password_charset(self) -> None:
        with self.assertRaises(util.BuildError):
            settings.BuildSettings(wifi_password="has space").validated()
        self.assertEqual(settings.BuildSettings(wifi_password="abc12345").validated().wifi_password, "abc12345")

    def test_ota_url(self) -> None:
        with self.assertRaises(util.BuildError):
            settings.BuildSettings(ota_url="ftp://example.com/ota.json").validated()
        value = settings.BuildSettings(ota_url="https://example.com/ota.json").validated()
        self.assertEqual(value.ota_url, "https://example.com/ota.json")

    def test_with_overrides_keeps_other_values(self) -> None:
        base = settings.BuildSettings()
        changed = base.with_overrides(ip="192.168.1.1", wifi_name=None)
        self.assertEqual(changed.ip, "192.168.1.1")
        self.assertEqual(changed.wifi_name, base.wifi_name)


class ProfilesTest(unittest.TestCase):
    def test_the_rg_x60_profiles_are_supported(self) -> None:
        self.assertEqual(
            profiles.names(),
            [
                "ruijie_rg-x60-new",
                "ruijie_rg-x60-new-2410",
                "ruijie_rg-x60-new-2410-homeproxy",
            ],
        )
        profile = profiles.require("ruijie_rg-x60-new")
        self.assertEqual(profile.device, "ruijie_rg-x60-new-mtkuboot")
        self.assertEqual(profile.config, "configs/ruijie-rg-x60-new.config")

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(util.BuildError):
            profiles.require("does-not-exist")

    def test_source_points_at_the_mtk_rebase(self) -> None:
        source = profiles.source(profiles.require("ruijie_rg-x60-new"))
        self.assertEqual(source.repo, "chasey-dev/immortalwrt-mt798x-rebase")
        self.assertEqual(source.ref, "25.12")
        self.assertTrue(source.gcc16)
        self.assertTrue(source.luci_patches)

    def test_the_2410_profile_uses_the_plain_device_name(self) -> None:
        profile = profiles.require("ruijie_rg-x60-new-2410")
        self.assertEqual(profile.device, "ruijie_rg-x60-new")
        self.assertEqual(profile.dt_model, "ruijie,rg-x60-new")
        self.assertEqual(profile.config, "configs/ruijie-rg-x60-new-2410.config")
        self.assertTrue((profiles.PROJECT_ROOT / profile.config).is_file())

    def test_the_2410_source_disables_gcc16_and_luci_patches(self) -> None:
        source = profiles.source(profiles.require("ruijie_rg-x60-new-2410"))
        self.assertEqual(source.repo, "padavanonly/immortalwrt-mt798x-6.6")
        self.assertEqual(source.ref, "openwrt-24.10-6.6")
        self.assertFalse(source.gcc16)
        self.assertFalse(source.luci_patches)

    def test_the_homeproxy_profile_reuses_the_2410_tree(self) -> None:
        plain = profiles.require("ruijie_rg-x60-new-2410")
        proxy = profiles.require("ruijie_rg-x60-new-2410-homeproxy")
        self.assertEqual(proxy.source, plain.source)
        self.assertEqual(proxy.device, plain.device)
        self.assertEqual(proxy.config, "configs/ruijie-rg-x60-new-2410-homeproxy.config")
        self.assertTrue((profiles.PROJECT_ROOT / proxy.config).is_file())


if __name__ == "__main__":
    unittest.main()
