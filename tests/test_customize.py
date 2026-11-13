from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from zerowrt_core import customize, state, util
from zerowrt_core.settings import BuildSettings

REPO_ROOT = Path(__file__).resolve().parents[1]


class CustomizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        self.assets = self.tree / "assets"
        self.assets.mkdir()
        (self.assets / "banner").write_text("ZeroWrt banner\n", encoding="utf-8")
        self.build_date = int(time.time())
        state.write(
            self.tree,
            {
                "build_date": self.build_date,
                "version": state.version_for(self.build_date),
                "device": "ruijie_rg-x60-new-mtkuboot",
                "profile": "ruijie_rg-x60-new",
                "dist": "ZeroWrt",
            },
        )
        self._write_tree()

    def _write(self, relative: str, text: str) -> None:
        path = self.tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def _write_tree(self) -> None:
        self._write(
            "include/version.mk",
            "VERSION_NUMBER:=$(call qstrip,$(CONFIG_VERSION_NUMBER))\n"
            "VERSION_NUMBER:=$(if $(VERSION_NUMBER),$(VERSION_NUMBER),25.12-SNAPSHOT)\n"
            "VERSION_DIST:=$(call qstrip,$(CONFIG_VERSION_DIST))\n"
            "VERSION_DIST:=$(if $(VERSION_DIST),$(VERSION_DIST),ImmortalWrt)\n"
            "VERSION_MANUFACTURER:=$(call qstrip,$(CONFIG_VERSION_MANUFACTURER))\n"
            "VERSION_MANUFACTURER:=$(if $(VERSION_MANUFACTURER),$(VERSION_MANUFACTURER),ImmortalWrt)\n",
        )
        self._write(
            "package/base-files/files/etc/openwrt_release",
            "DISTRIB_ID='%D'\nDISTRIB_RELEASE='%V'\nDISTRIB_DESCRIPTION='%D %V %C'\n"
            "DISTRIB_REVISION='%R'\n",
        )
        self._write(
            "package/base-files/files/usr/lib/os-release",
            'NAME="%D"\nOPENWRT_RELEASE="%D %V %C"\n',
        )
        self._write(
            "package/base-files/files/bin/config_generate",
            'lan) ipad=${ipaddr:-"192.168.1.1"} ;;\n'
            "set system.@system[-1].hostname='ImmortalWrt'\n",
        )
        self._write("package/base-files/files/etc/shadow", "root:::0:99999:7:::\n")
        self._write(
            "package/base-files/files/etc/passwd",
            "root:x:0:0:root:/root:/bin/ash\n",
        )
        self._write(
            "package/base-files/files/etc/profile",
            'export PATH="%PATH%"\n'
            "export PS1='\\u@\\h:\\w\\$ '\n"
            "export ENV=/etc/shinit\n",
        )
        self._write(
            "feeds/packages/utils/ttyd/files/ttyd.config",
            "config ttyd\n\toption interface '@lan'\n\toption command '/bin/login'\n",
        )
        self._write(
            "feeds/packages/utils/ttyd/files/ttyd.init",
            "procd_set_param stdout 1\nprocd_set_param stderr 1\n",
        )
        self._write(
            "feeds/luci/applications/luci-app-ttyd/root/usr/share/luci/menu.d/luci-app-ttyd.json",
            json.dumps(
                {
                    "admin/system/ttyd": {"title": "Terminal", "order": 1},
                    "admin/system/ttyd/ttyd": {"title": "Terminal", "order": 1},
                },
                indent="\t",
            )
            + "\n",
        )
        self._write(
            "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc",
            'return { ssid: "ImmortalWrt-2.4G" };\n'
            'return { ssid: "ImmortalWrt-5G" };\n'
            'return { ssid: "ImmortalWrt-6G" };\n'
            '        "encryption": "none"\n',
        )

    def _apply(self, settings: BuildSettings | None = None) -> None:
        with mock.patch.object(customize, "_openssl_hash", return_value="$5$fake$hash"):
            customize.apply(
                self.tree,
                settings or BuildSettings(),
                assets=self.assets,
                repo_root=REPO_ROOT,
            )

    def test_apply_and_verify(self) -> None:
        self._apply()
        version = state.version_for(self.build_date)
        self.assertIn(f"VERSION_NUMBER:={version}", (self.tree / "include/version.mk").read_text())
        self.assertIn(
            "VERSION_DIST:=$(if $(VERSION_DIST),$(VERSION_DIST),ZeroWrt)",
            (self.tree / "include/version.mk").read_text(),
        )
        self.assertIn("10.0.0.1", (self.tree / "package/base-files/files/bin/config_generate").read_text())
        self.assertIn("hostname='ZeroWrt'", (self.tree / "package/base-files/files/bin/config_generate").read_text())
        self.assertIn("root:$5$fake$hash:", (self.tree / "package/base-files/files/etc/shadow").read_text())
        self.assertIn("/bin/bash", (self.tree / "package/base-files/files/etc/passwd").read_text())
        self.assertIn("/opt/usr/sbin", (self.tree / "package/base-files/files/etc/profile").read_text())
        ddns_script = self.tree / "package/base-files/files/usr/lib/ddns/get_ipv6.sh"
        self.assertTrue(ddns_script.is_file())
        self.assertIn("preferred_lft", ddns_script.read_text(encoding="utf-8"))
        self.assertTrue(os.access(ddns_script, os.X_OK))
        self.assertEqual(
            (self.tree / "package/base-files/files/etc/banner").read_text(),
            "ZeroWrt banner\n",
        )
        self.assertIn(
            "option command '/bin/login -f root'",
            (self.tree / "feeds/packages/utils/ttyd/files/ttyd.config").read_text(),
        )
        self.assertIn("procd_set_param stdout 0", (self.tree / "feeds/packages/utils/ttyd/files/ttyd.init").read_text())
        wifi = (self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc").read_text()
        self.assertIn('ssid: "ZeroWrt-2.4G"', wifi)
        self.assertIn('"encryption": "psk2"', wifi)
        self.assertIn('"key": "1234567890"', wifi)

    def test_apply_is_idempotent(self) -> None:
        self._apply()
        self._apply()
        version = state.version_for(self.build_date)
        self.assertIn(f"VERSION_NUMBER:={version}", (self.tree / "include/version.mk").read_text())

    def test_custom_ssid_and_password(self) -> None:
        self._apply(BuildSettings(wifi_name="Lab", wifi_password="abcdefgh"))
        wifi = (self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc").read_text()
        self.assertIn('ssid: "Lab-5G"', wifi)
        self.assertIn('"key": "abcdefgh"', wifi)

    def test_missing_mtwifi_is_skipped(self) -> None:
        """The 24.10 tree ships the Lua WiFi front end, so this file is absent."""
        (self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc").unlink()
        self._apply()
        self.assertFalse(
            (self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc").exists()
        )

    def _write_shell_wifi(self) -> Path:
        """Replace the 25.12 ucode front end with the 24.10 shell driver."""
        (self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc").unlink()
        relative = "package/mtk/applications/mtwifi-cfg/files/mtwifi.sh"
        self._write(
            relative,
            "#!/bin/sh\n"
            'append DRIVERS "mtwifi"\n'
            "\n"
            "detect_mtwifi() {\n"
            '\tif [ -d "/sys/module/mt_wifi" ]; then\n'
            "\t\t\t\t\tssid=\"ImmortalWrt-2.4G\"\n"
            "\t\t\t\telse\n"
            "\t\t\t\t\tssid=\"ImmortalWrt-5G\"\n"
            "\t\t\t\tfi\n"
            "\t\t\t\tuci -q batch <<-EOF\n"
            "\t\t\t\t\tset wireless.${dev}=wifi-device\n"
            "\t\t\t\t\tset wireless.default_${dev}.ssid=${ssid}\n"
            "\t\t\t\t\tset wireless.default_${dev}.encryption=none\n"
            "EOF\n"
            "\tfi\n"
            "}\n",
        )
        return self.tree / relative

    def test_wifi_defaults_from_the_2410_shell_driver(self) -> None:
        path = self._write_shell_wifi()
        self._apply()
        wifi = path.read_text()
        self.assertIn('ssid="ZeroWrt-2.4G"', wifi)
        self.assertIn('ssid="ZeroWrt-5G"', wifi)
        self.assertIn("encryption=psk2", wifi)
        self.assertIn("set wireless.default_${dev}.key=${MTWIFI_KEY}", wifi)
        self.assertIn("MTWIFI_KEY='1234567890'", wifi)
        self.assertNotIn("ImmortalWrt-", wifi)
        self.assertNotIn("encryption=none", wifi)

    def test_shell_wifi_keeps_password_metacharacters_literal(self) -> None:
        path = self._write_shell_wifi()
        self._apply(BuildSettings(wifi_name="Lab", wifi_password="pa$$w0rd!"))
        wifi = path.read_text()
        self.assertIn('ssid="Lab-5G"', wifi)
        # Single quotes keep the dollar signs from being expanded at boot.
        self.assertIn("MTWIFI_KEY='pa$$w0rd!'", wifi)

    def test_wrong_default_ssid_fails(self) -> None:
        path = self.tree / "package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc"
        path.write_text('return { ssid: "Something-2.4G" };\n', encoding="utf-8")
        with self.assertRaises(util.BuildError):
            self._apply()


if __name__ == "__main__":
    unittest.main()
