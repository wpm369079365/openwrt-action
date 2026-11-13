"""In-tree customisation: branding, default address, password, shell, ttyd, WiFi.

This is the Python replacement for the vendor ``diy-script.sh``. Every edit is
anchored on a small, named pattern and verified afterwards; a silently missed
``sed`` expression is how a firmware ships with the wrong default IP or an open
WiFi network.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import state, util
from .settings import BuildSettings

VERSION_MK = Path("include/version.mk")
CONFIG_GENERATE = Path("package/base-files/files/bin/config_generate")
OPENWRT_RELEASE = Path("package/base-files/files/etc/openwrt_release")
OS_RELEASE = Path("package/base-files/files/usr/lib/os-release")
BANNER = Path("package/base-files/files/etc/banner")
SHADOW = Path("package/base-files/files/etc/shadow")
PASSWD = Path("package/base-files/files/etc/passwd")
PROFILE = Path("package/base-files/files/etc/profile")
TTYD_CONFIG = Path("feeds/packages/utils/ttyd/files/ttyd.config")
TTYD_INIT = Path("feeds/packages/utils/ttyd/files/ttyd.init")
TTYD_MENU = Path("feeds/luci/applications/luci-app-ttyd/root/usr/share/luci/menu.d/luci-app-ttyd.json")
MTWIFI = Path("package/mtk/applications/mtwifi-cfg-ucode/files/lib/wifi/mtwifi.uc")
# 24.10 has no ucode front end; the first-boot ``wireless`` config is assembled
# by this shell driver instead, so its defaults are the ones worth patching.
MTWIFI_SHELL = Path("package/mtk/applications/mtwifi-cfg/files/mtwifi.sh")

IPAD_RE = re.compile(r'ipad=\$\{ipaddr:-"[^"]*"\}')
HOSTNAME_RE = re.compile(r"hostname='(?:LEDE|OpenWrt|ImmortalWrt|ZeroWrt)'")
VERSION_VAR_RE = re.compile(
    r"^(?P<var>VERSION_DIST|VERSION_MANUFACTURER):=\$\(if \$\((?P=var)\),\$\((?P=var)\),[^)\n]+\)",
    re.M,
)
VERSION_NUMBER_BLOCK_RE = re.compile(
    r"VERSION_NUMBER:=\$\(call qstrip,\$\(CONFIG_VERSION_NUMBER\)\)\s*\n"
    r"VERSION_NUMBER:=\$\(if \$\(VERSION_NUMBER\),\$\(VERSION_NUMBER\),[^)\n]+\)"
)

PROMPT = r"""export PS1='\[\e[32;1m\][\u@\h\[\e[0m\] \[\033[01;34m\]\W\[\033[00m\]\[\e[32;1m\]]\[\e[0m\]\$ '"""


def _tree(tree: Path) -> Path:
    return Path(tree)


def _require(path: Path) -> None:
    if not path.exists():
        raise util.BuildError(f"missing {path}; the source tree layout changed")


def patch_version_mk(tree: Path, *, dist: str, version: str) -> None:
    tree = _tree(tree)
    path = tree / VERSION_MK
    _require(path)
    util.header("Patching include/version.mk")

    text = util.read_text(path)
    replacements = 0
    for match in VERSION_VAR_RE.finditer(text):
        variable = match.group("var")
        text = text.replace(
            match.group(0),
            f"{variable}:=$(if $({variable}),$({variable}),{dist})",
        )
        replacements += 1
    if replacements != 2:
        raise util.BuildError(f"{VERSION_MK}: VERSION_DIST/VERSION_MANUFACTURER defaults were not found")

    if f"VERSION_NUMBER:={version}" in text:
        count = 1
    else:
        text, count = VERSION_NUMBER_BLOCK_RE.subn(f"VERSION_NUMBER:={version}", text)
        if count != 1:
            # A slightly different upstream layout still has the second line.
            text, count = re.subn(
                r"^(VERSION_NUMBER:=\$\(if \$\(VERSION_NUMBER\),\$\(VERSION_NUMBER\),)[^)\n]+(\))",
                rf"\g<1>{version}\g<2>",
                text,
                flags=re.M,
            )
    if count != 1:
        raise util.BuildError(f"{VERSION_MK}: VERSION_NUMBER definition was not found")
    util.write_text(path, text)
    util.log(f"VERSION_DIST/MANUFACTURER -> {dist}; VERSION_NUMBER -> {version}")


def patch_release_files(tree: Path, *, dist: str, version: str) -> None:
    tree = _tree(tree)
    release = tree / OPENWRT_RELEASE
    os_release = tree / OS_RELEASE
    _require(release)
    _require(os_release)
    util.header("Patching release strings")

    text = util.read_text(release)
    text, description = re.subn(
        r"^DISTRIB_DESCRIPTION=.*$",
        f"DISTRIB_DESCRIPTION='{dist}-{version}'",
        text,
        flags=re.M,
    )
    text, revision = re.subn(
        r"^DISTRIB_REVISION=.*$",
        "DISTRIB_REVISION=' By MinimaxFlora'",
        text,
        flags=re.M,
    )
    if description != 1 or revision != 1:
        raise util.BuildError(f"{OPENWRT_RELEASE}: DISTRIB_DESCRIPTION/REVISION lines were not found")
    util.write_text(release, text)

    release_line = f'OPENWRT_RELEASE="{dist} 标准版 @R{version} BY MinimaxFlora"'
    text = util.read_text(os_release)
    text, count = re.subn(r"^OPENWRT_RELEASE=.*$", release_line, text, flags=re.M)
    if count != 1:
        raise util.BuildError(f"{OS_RELEASE}: OPENWRT_RELEASE line was not found")
    util.write_text(os_release, text)
    util.log(f"release strings -> {dist}-{version}")


def patch_config_generate(tree: Path, *, ip: str, hostname: str) -> None:
    tree = _tree(tree)
    path = tree / CONFIG_GENERATE
    _require(path)
    util.header("Patching config_generate (LAN IP / hostname)")

    text = util.read_text(path)
    text, ip_count = IPAD_RE.subn(f'ipad=${{ipaddr:-"{ip}"}}', text)
    text, host_count = HOSTNAME_RE.subn(f"hostname='{hostname}'", text)
    if ip_count == 0:
        raise util.BuildError(f"{CONFIG_GENERATE}: no default LAN address was found")
    if host_count == 0:
        raise util.BuildError(f"{CONFIG_GENERATE}: no default hostname was found")
    util.write_text(path, text)
    util.log(f"default LAN IP -> {ip}; hostname -> {hostname} ({ip_count} address defaults)")


def patch_banner(tree: Path, assets: Path) -> None:
    tree = _tree(tree)
    source = Path(assets) / "banner"
    if not source.is_file():
        raise util.BuildError(f"banner asset not found: {source}")
    destination = tree / BANNER
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    util.log(f"login banner: {BANNER}")


def _openssl_hash(password: str) -> str:
    if util.which("openssl") is None:
        raise util.BuildError("openssl is required to generate the root password hash")
    code, output = util.run(
        ["openssl", "passwd", "-5", "-stdin"],
        input_text=password + "\n",
        capture=True,
        quiet=True,
        check=False,
    )
    digest = output.strip().splitlines()[-1] if output.strip() else ""
    if code != 0 or not digest.startswith("$5$"):
        raise util.BuildError("openssl could not generate a SHA-256 crypt password hash")
    return digest


def patch_root_password(tree: Path, password: str) -> None:
    tree = _tree(tree)
    path = tree / SHADOW
    _require(path)
    util.header("Setting the default root password")
    digest = _openssl_hash(password)
    text = util.read_text(path)
    text, count = re.subn(r"^root:[^:]*:", f"root:{digest}:", text, count=1, flags=re.M)
    if count != 1:
        raise util.BuildError(f"{SHADOW}: root entry was not found")
    util.write_text(path, text)
    util.log("root password hash written to package/base-files/files/etc/shadow")


def patch_profile(tree: Path, *, repo_root: Path, dist: str) -> None:
    tree = _tree(tree)
    path = tree / PROFILE
    _require(path)
    util.header("Patching the login shell profile")

    text = util.read_text(path)
    if ":/opt/usr/sbin" not in text:
        text, count = re.subn(
            r'^export PATH="%PATH%"$',
            'export PATH="%PATH%:/opt/bin:/opt/sbin:/opt/usr/bin:/opt/usr/sbin"',
            text,
            flags=re.M,
        )
        if count != 1:
            raise util.BuildError(f"{PROFILE}: PATH line was not found")
    if PROMPT not in text:
        text, count = re.subn(r"^export PS1='\\u@\\h:\\w\\\$ '$", lambda _m: PROMPT, text, flags=re.M)
        if count != 1:
            raise util.BuildError(f"{PROFILE}: default PS1 was not found")
    if "export TERM=xterm-color" not in text:
        text = text.replace("export ENV=/etc/shinit", "export TERM=xterm-color\nexport ENV=/etc/shinit", 1)
    if "export HISTCONTROL=ignoredups" not in text:
        text = text.replace(
            "export ENV=/etc/shinit",
            "export ENV=/etc/shinit\nexport HISTCONTROL=ignoredups",
            1,
        )
    util.write_text(path, text)

    passwd = tree / PASSWD
    _require(passwd)
    passwd_text = util.read_text(passwd)
    passwd_text, count = re.subn(
        r"^(root:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:)[^:\n]*$",
        r"\g<1>/usr/bin/bash",
        passwd_text,
        count=1,
        flags=re.M,
    )
    if count != 1:
        raise util.BuildError(f"{PASSWD}: root shell entry was not found")
    util.write_text(passwd, passwd_text)

    for name in (".bashrc", ".bash_profile"):
        source = Path(repo_root) / "scripts" / name
        if not source.is_file():
            raise util.BuildError(f"shell asset not found: {source}")
        destination = tree / "package" / "base-files" / "files" / "root" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    util.log(f"shell -> bash, prompt and {dist} PATH updated")


def patch_ttyd(tree: Path) -> None:
    tree = _tree(tree)
    util.header("Patching ttyd (auto login, menu order, quiet init)")
    changed = 0

    config = tree / TTYD_CONFIG
    if config.exists():
        text = util.read_text(config)
        if "option command '/bin/login -f root'" not in text:
            text, count = re.subn(
                r"^(\s*option\s+command\s+)'/bin/login'\s*$",
                r"\g<1>'/bin/login -f root'",
                text,
                flags=re.M,
            )
            if count:
                util.write_text(config, text)
                changed += 1
    else:
        util.warn(f"missing {TTYD_CONFIG}; skipping ttyd config")

    init = tree / TTYD_INIT
    if init.exists():
        text = util.read_text(init)
        new = text.replace("procd_set_param stdout 1", "procd_set_param stdout 0")
        new = new.replace("procd_set_param stderr 1", "procd_set_param stderr 0")
        if new != text:
            util.write_text(init, new)
            changed += 1
    else:
        util.warn(f"missing {TTYD_INIT}; skipping ttyd init")

    menu = tree / TTYD_MENU
    if menu.exists():
        data = json.loads(util.read_text(menu))
        if isinstance(data, dict):
            if "admin/services/ttyd" in data and "admin/system/ttyd" not in data:
                data["admin/system/ttyd"] = data.pop("admin/services/ttyd")
            entry = data.get("admin/system/ttyd")
            if isinstance(entry, dict) and entry.get("order") != 50:
                entry["order"] = 50
                util.write_text(menu, json.dumps(data, indent="\t", ensure_ascii=False) + "\n")
                changed += 1
    else:
        util.warn(f"missing {TTYD_MENU}; skipping ttyd menu")

    if changed == 0:
        util.log("ttyd configuration already up to date")
    else:
        util.log(f"ttyd customisations applied ({changed} file(s))")


def _wifi_defaults_file(tree: Path) -> Path | None:
    """Return the tree-relative file that seeds the factory WiFi defaults.

    25.12 patches the ucode front end; 24.10 only has the shell driver that
    builds ``wireless`` from a heredoc on first boot. Both are patchable, the
    anchors just differ.
    """
    for relative in (MTWIFI, MTWIFI_SHELL):
        if (Path(tree) / relative).is_file():
            return relative
    return None


def patch_wifi(tree: Path, settings: BuildSettings) -> None:
    tree = _tree(tree)
    relative = _wifi_defaults_file(tree)
    # Neither front end present means this is a source tree we do not know; the
    # image keeps whatever upstream generates and the SSID is set in LuCI.
    if relative is None:
        util.warn(
            f"neither {MTWIFI} nor {MTWIFI_SHELL} is in this tree; leaving the default "
            "WiFi configuration alone (set the SSID and password in LuCI after flashing)"
        )
        return
    util.header("Patching default WiFi configuration")

    path = tree / relative
    expected = {band: settings.ssid(band) for band in ("2g", "5g", "6g")}
    if relative == MTWIFI_SHELL:
        _patch_wifi_shell(path, expected, settings.wifi_password)
        util.log(f"SSIDs: {expected['2g']} / {expected['5g']}")
        return

    text = util.read_text(path)
    labels = {"2.4G": "2g", "5G": "5g", "6G": "6g"}
    for label, band in labels.items():
        text, count = re.subn(
            rf'ssid: "ImmortalWrt-{re.escape(label)}"',
            f'ssid: "{expected[band]}"',
            text,
        )
        if count:
            continue
        # An already patched file has the configured SSID; anything else is a
        # layout we do not understand. The RG-X60 is dual-band, so a missing
        # 6 GHz default is not fatal.
        if f'ssid: "{expected[band]}"' in text:
            continue
        if band == "6g":
            util.warn(f"{MTWIFI}: no 6 GHz default SSID; skipping that band")
            continue
        raise util.BuildError(f"{MTWIFI}: default {label} SSID was not found")

    key = settings.wifi_password
    if '"encryption": "psk2"' in text and f'"key": "{key}"' in text:
        util.log("default WiFi configuration already up to date")
    else:
        text, count = re.subn(
            r'(?P<indent>[ \t]*)"encryption": "none"',
            lambda m: f'{m.group("indent")}"encryption": "psk2",\n{m.group("indent")}"key": "{key}"',
            text,
            count=1,
        )
        if count == 0:
            text, count = re.subn(
                r'(?P<indent>[ \t]*)"key": "[^"]*"',
                lambda m: f'{m.group("indent")}"key": "{key}"',
                text,
                count=1,
            )
        if count == 0:
            raise util.BuildError(f"{MTWIFI}: open default WiFi network was not found")
        util.write_text(path, text)

    final = util.read_text(path)
    for _band, ssid in expected.items():
        if f'ssid: "{ssid}"' not in final:
            raise util.BuildError(f"{MTWIFI}: SSID {ssid} was not applied")
    if '"encryption": "psk2"' not in final or f'"key": "{key}"' not in final:
        raise util.BuildError(f"{MTWIFI}: WPA2 key was not applied")
    if "ImmortalWrt-" in final:
        raise util.BuildError(f"{MTWIFI}: an upstream SSID is still present")
    util.log(f"SSIDs: {expected['2g']} / {expected['5g']} / {expected['6g']}")


def _patch_wifi_shell(path: Path, expected: dict[str, str], key: str) -> None:
    """Patch the 24.10 ``mtwifi.sh`` driver defaults.

    That script assembles the first-boot ``wireless`` config from a heredoc, so
    the SSID is a plain shell assignment and the interface key is never written
    at all -- upstream ships an open network. The key goes into a single-quoted
    variable at the top of the file: the heredoc expands it at boot, and quoting
    keeps a ``$`` or a backtick in the password literal.
    """
    text = util.read_text(path)
    labels = {"2.4G": "2g", "5G": "5g", "6G": "6g"}
    for label, band in labels.items():
        text, count = re.subn(
            rf'ssid="ImmortalWrt-{re.escape(label)}"',
            f'ssid="{expected[band]}"',
            text,
        )
        if count:
            continue
        if f'ssid="{expected[band]}"' in text:
            continue
        # The RG-X60 is dual-band, so a missing 6 GHz default is not fatal.
        if band == "6g":
            continue
        raise util.BuildError(f"{path}: default {label} SSID was not found")

    anchor = 'append DRIVERS "mtwifi"'
    if f"MTWIFI_KEY='{key}'" not in text:
        if anchor not in text:
            raise util.BuildError(f'{path}: "{anchor}" was not found')
        text = text.replace(anchor, f"{anchor}\n\nMTWIFI_KEY='{key}'", 1)

    pattern = re.compile(
        r"^(?P<indent>[ \t]*)set wireless\.default_\$\{dev\}\.encryption=none\s*$",
        re.M,
    )
    replacement = (
        r"\g<indent>set wireless.default_${dev}.encryption=psk2\n"
        r"\g<indent>set wireless.default_${dev}.key=${MTWIFI_KEY}"
    )
    text, count = pattern.subn(replacement, text, count=1)
    if count == 0 and "encryption=psk2" not in text:
        raise util.BuildError(f"{path}: the open default WiFi network was not found")
    util.write_text(path, text)

    final = util.read_text(path)
    for band in ("2g", "5g"):
        if f'ssid="{expected[band]}"' not in final:
            raise util.BuildError(f"{path}: SSID {expected[band]} was not applied")
    if "encryption=psk2" not in final or "${MTWIFI_KEY}" not in final:
        raise util.BuildError(f"{path}: WPA2 key was not applied")
    if "ImmortalWrt-" in final:
        raise util.BuildError(f"{path}: an upstream SSID is still present")


def apply(tree: Path, settings: BuildSettings, *, assets: Path, repo_root: Path) -> None:
    tree = _tree(tree)
    info = state.read(tree)
    build_date = state.build_date(info)
    if build_date <= 0:
        raise util.BuildError("build date is missing; run 'config apply' before customising")
    version = info.get("version") or state.version_for(build_date)

    patch_version_mk(tree, dist=settings.dist_name, version=version)
    patch_release_files(tree, dist=settings.dist_name, version=version)
    patch_config_generate(tree, ip=settings.ip, hostname=settings.dist_name)
    patch_banner(tree, assets)
    patch_root_password(tree, settings.root_password)
    patch_profile(tree, repo_root=repo_root, dist=settings.dist_name)
    patch_ttyd(tree)
    patch_wifi(tree, settings)
    verify(tree, settings, assets=assets)


def verify(tree: Path, settings: BuildSettings, *, assets: Path) -> None:
    tree = _tree(tree)
    util.header("Verifying customisations")
    problems: list[str] = []

    version = state.version_for(state.build_date(state.read(tree)))
    version_text = util.read_text(tree / VERSION_MK)
    if f"VERSION_DIST:=$(if $(VERSION_DIST),$(VERSION_DIST),{settings.dist_name})" not in version_text:
        problems.append("VERSION_DIST default is wrong")
    manufacturer = f"VERSION_MANUFACTURER:=$(if $(VERSION_MANUFACTURER),$(VERSION_MANUFACTURER),{settings.dist_name})"
    if manufacturer not in version_text:
        problems.append("VERSION_MANUFACTURER default is wrong")
    if f"VERSION_NUMBER:={version}" not in version_text:
        problems.append(f"VERSION_NUMBER is not {version}")

    release = util.read_text(tree / OPENWRT_RELEASE)
    if f"DISTRIB_DESCRIPTION='{settings.dist_name}-{version}'" not in release:
        problems.append("DISTRIB_DESCRIPTION is wrong")
    if "DISTRIB_REVISION=' By MinimaxFlora'" not in release:
        problems.append("DISTRIB_REVISION is wrong")
    expected_release = (
        f'OPENWRT_RELEASE="{settings.dist_name} 标准版 @R{version} BY MinimaxFlora"'
    )
    if expected_release not in util.read_text(tree / OS_RELEASE):
        problems.append("OPENWRT_RELEASE is wrong")

    generate = util.read_text(tree / CONFIG_GENERATE)
    addresses = set(re.findall(r'ipad=\$\{ipaddr:-"([^"]*)"\}', generate))
    wrong = sorted(value for value in addresses if value != settings.ip)
    if wrong:
        problems.append(f"config_generate still defaults to {', '.join(wrong)}")
    if f"hostname='{settings.dist_name}'" not in generate:
        problems.append("config_generate hostname is wrong")

    banner = tree / BANNER
    if not banner.exists() or banner.read_bytes() != (Path(assets) / "banner").read_bytes():
        problems.append("login banner does not match assets/banner")

    shadow = util.read_text(tree / SHADOW)
    if not re.search(r"^root:\$5\$", shadow, re.M):
        problems.append("root password hash is not SHA-256 crypt")

    passwd = util.read_text(tree / PASSWD)
    if not re.search(r"^root:.*:/usr/bin/bash$", passwd, re.M):
        problems.append("root shell is not bash")

    profile = util.read_text(tree / PROFILE)
    for needle in ("/opt/usr/sbin", "export TERM=xterm-color", "export HISTCONTROL=ignoredups"):
        if needle not in profile:
            problems.append(f"profile is missing {needle!r}")

    ttyd = tree / TTYD_CONFIG
    if ttyd.exists() and "option command '/bin/login -f root'" not in util.read_text(ttyd):
        problems.append("ttyd auto login is not configured")

    relative = _wifi_defaults_file(tree)
    if relative is not None:
        wifi = util.read_text(tree / relative)
        shell = relative == MTWIFI_SHELL
        for band in ("2g", "5g"):
            ssid = settings.ssid(band)
            needle = f'ssid="{ssid}"' if shell else f'ssid: "{ssid}"'
            if needle not in wifi:
                problems.append(f"WiFi SSID {ssid} is missing")
        marker = "encryption=psk2" if shell else '"encryption": "psk2"'
        if marker not in wifi:
            problems.append("WiFi encryption is not WPA2")

    if problems:
        raise util.BuildError("customisation failed:\n  - " + "\n  - ".join(problems))
    util.log("customisations verified")
