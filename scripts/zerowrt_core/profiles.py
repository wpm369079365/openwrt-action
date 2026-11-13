"""Device profiles and the upstream tree they are built from.

The firmware is built from ``padavanonly/immortalwrt-mt798x-6.6`` (ImmortalWrt
24.10, MTK wireless, GCC 14); the configuration file under ``configs/`` is the
authoritative package selection. Trees maintained elsewhere are not wired in:
a build has to come from that repository so the WiFi driver and the kernel are
the ones this device was validated on.

The profile name doubles as the GitHub release tag and is deliberately kept
independent from the upstream ``Device/`` name: upstream may rename the device
without changing the update-index key our users already have.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import util

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Source:
    """An upstream build tree.

    ``gcc16`` and ``luci_patches`` describe what the tree is: the 25.12 rebase
    ships GCC 16 and a LuCI feed the five patches were refreshed against, while
    the 24.10 tree is GCC 14 and has a different LuCI. Both flags gate work that
    would otherwise fail on the tree it does not belong to. The 24.10 tree is
    the only one wired in, so both are off; the flags stay as the switch that
    makes a different tree buildable again.

    ``ota`` says whether this tree's image carries ``luci-app-ota``. It is a
    ZeroWrt addition, so a build that leaves it out must also skip the package
    selection, the embedded metadata and the published index.
    """

    key: str
    repo: str
    ref: str
    label: str
    gcc16: bool = True
    luci_patches: bool = True
    ota: bool = True


@dataclass(frozen=True)
class DeviceProfile:
    """A buildable device: config file, target and the source tree it comes from."""

    name: str
    config: str
    source: str
    board: str
    subtarget: str
    device: str
    dt_model: str
    vendor: str
    model: str
    layout: str = ""

    @property
    def release_tag(self) -> str:
        return self.name


SOURCES: dict[str, Source] = {
    # The only supported tree: padavanonly's ImmortalWrt 24.10 for MT798x. It
    # carries the MTK wireless stack (mt_wifi 7.6.6.1) on a 6.6 kernel and ships
    # a ruijie_rg-x60-new device whose partition map (UBI at 0x680000, 107 MB)
    # and sysupgrade-tar format match the MTK U-Boot layout. It is GCC 14 and
    # its LuCI feed is a different vintage than 25.12, hence both capability
    # flags off. ``luci-app-ota`` handles opkg, so OTA support is kept.
    "immortalwrt-mtk-2410": Source(
        key="immortalwrt-mtk-2410",
        repo="padavanonly/immortalwrt-mt798x-6.6",
        ref="openwrt-24.10-6.6",
        label="ImmortalWrt 24.10 (MT798x 6.6, MTK wireless)",
        gcc16=False,
        luci_patches=False,
    ),
}

PROFILES: dict[str, DeviceProfile] = {
    # The upstream device name has no ``-mtkuboot`` suffix on this tree; the
    # partition map and sysupgrade format are what the flash layout expects.
    "ruijie_rg-x60-new-2410": DeviceProfile(
        name="ruijie_rg-x60-new-2410",
        config="configs/ruijie-rg-x60-new-2410.config",
        source="immortalwrt-mtk-2410",
        board="mediatek",
        subtarget="filogic",
        device="ruijie_rg-x60-new",
        dt_model="ruijie,rg-x60-new",
        vendor="Ruijie",
        model="RG-X60 New",
        layout="MTK U-Boot layout (sysupgrade-tar)",
    ),
    # Same hardware and tree as the plain 2410 profile, with luci-app-homeproxy
    # (sing-box) added; ships as its own release tag so the proxy-free image
    # keeps its published files.
    "ruijie_rg-x60-new-2410-homeproxy": DeviceProfile(
        name="ruijie_rg-x60-new-2410-homeproxy",
        config="configs/ruijie-rg-x60-new-2410-homeproxy.config",
        source="immortalwrt-mtk-2410",
        board="mediatek",
        subtarget="filogic",
        device="ruijie_rg-x60-new",
        dt_model="ruijie,rg-x60-new",
        vendor="Ruijie",
        model="RG-X60 New",
        layout="MTK U-Boot layout (sysupgrade-tar)",
    ),
}

DEFAULT_DEVICE = "ruijie_rg-x60-new-2410-homeproxy"


def names() -> list[str]:
    return list(PROFILES)


def find(name: str | None) -> DeviceProfile | None:
    return PROFILES.get(name or "")


def require(name: str) -> DeviceProfile:
    profile = find(name)
    if profile is None:
        raise util.BuildError(f"unknown device profile {name!r}; expected one of: {', '.join(names())}")
    return profile


def source(profile: DeviceProfile) -> Source:
    return SOURCES[profile.source]


def config_path(profile: DeviceProfile, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else PROJECT_ROOT
    return base / profile.config
