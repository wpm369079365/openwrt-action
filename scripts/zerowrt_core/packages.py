"""External packages and feed conflict removal.

Everything cloned by this module lives under ``package/new`` (or in the feed
directory it replaces). Whichever feed ships a package with the same name loses
it to :func:`prune_feed_conflicts`; a duplicate package makes the buildroot
pick one silently, which is how the wrong Clash core or an old Argon theme
ends up in an image.

The package list itself is not duplicated here: ``configs/ruijie-rg-x60-new.config``
stays the authoritative selection. This module only makes sure every name the
config asks for exists in the tree.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import util

# One checkout carries the LuCI front ends, the proxy cores, the theme and the
# extra applications used by the config.
HELLOWORLD_REPO = "MinimaxFlora/openwrt_package"
HELLOWORLD_REF = "master"

# The Go/Rust versions the 25.12 tree expects are newer than the feed copies.
GOLANG_REPO = "sbwml/packages_lang_golang"
GOLANG_REF = "27.x"
RUST_REPO = "sbwml/packages_lang_rust"
RUST_REF = "main"

# Docker is not selected by the default config but the workflow keeps the same
# package set as the MediaTek builder so enabling it is a one-line config edit.
DOCKERMAN_REPO = "sbwml/luci-app-dockerman"
DOCKERMAN_REF = "openwrt-25.12"
DOCKER_REPOS = {
    "docker": ("sbwml/packages_utils_docker", "main"),
    "dockerd": ("sbwml/packages_utils_dockerd", "main"),
    "containerd": ("sbwml/packages_utils_containerd", "main"),
    "runc": ("sbwml/packages_utils_runc", "main"),
}

OTA_REPO = "MinimaxFlora/luci-app-ota"
OTA_REF = "main"

# AList's LuCI package was archived in June 2025 and points at OpenList as its
# successor. The OpenList repository carries the Go service and the LuCI front
# end in two sibling directories, which is the layout the buildroot wants one
# level below ``package/new``. The front end is built from the *lite* dist so
# the image does not grow by another ~20 MiB of static assets.
OPENLIST_REPO = "sbwml/luci-app-openlist2"
OPENLIST_REF = "main"

BUILD_PACKAGE_RE = re.compile(r"call BuildPackage,([^)\n]+)\)")
PKG_NAME_RE = re.compile(r"^PKG_NAME\s*:?=\s*(\S+)", re.M)


def declared_package_names(directory: Path) -> set[str]:
    """Names a package directory registers with the buildroot.

    A single Makefile can build several packages (helloworld's
    ``v2ray-geodata`` registers ``v2ray-geoip`` and ``v2ray-geosite``), while
    LuCI Makefiles write ``BuildPackage,$(PKG_NAME)``. Comparing directory
    names alone therefore misses real conflicts.
    """
    makefile = Path(directory) / "Makefile"
    if not makefile.is_file():
        return set()

    text = util.read_text(makefile)
    match = PKG_NAME_RE.search(text)
    pkg_name = match.group(1) if match else ""

    names: set[str] = set()
    for raw in BUILD_PACKAGE_RE.findall(text):
        name = raw.replace("$(PKG_NAME)", pkg_name).strip()
        if not name or "$" in name:
            continue
        names.add(name)
    if pkg_name:
        names.add(pkg_name)
    return names


def new_package_names(tree: Path) -> set[str]:
    """Every package name owned by ``package/new``."""
    names: set[str] = set()
    new_dir = Path(tree) / "package" / "new"
    if not new_dir.is_dir():
        return names
    for root, dirs, _files in os.walk(new_dir, topdown=True, followlinks=False):
        for name in dirs:
            candidate = Path(root) / name
            names |= declared_package_names(candidate)
    return names


def _prune_packages(base: Path, names: set[str], *, removed: list[Path]) -> None:
    """Delete every directory under *base* that provides one of ``names``."""
    if not base.exists():
        return
    for root, dirs, _files in os.walk(base, topdown=True, followlinks=False):
        keep = []
        for name in dirs:
            candidate = Path(root) / name
            is_package = (candidate / "Makefile").is_file()
            if is_package and (candidate.name in names or declared_package_names(candidate) & names):
                if util.remove_path(candidate):
                    removed.append(candidate)
                continue
            keep.append(name)
        dirs[:] = keep


def prune_feed_conflicts(tree: Path) -> list[Path]:
    """Remove feed copies of every package cloned into ``package/new``.

    Both the raw feed checkouts and the ``package/feeds`` links are pruned:
    the link is what the buildroot actually scans, and a stale link survives a
    removal from the feed checkout.
    """
    tree = Path(tree)
    names = new_package_names(tree)
    removed: list[Path] = []
    if not names:
        return removed
    _prune_packages(tree / "feeds", names, removed=removed)
    _prune_packages(tree / "package" / "feeds", names, removed=removed)
    for path in removed:
        util.log(f"removed feed package {path.relative_to(tree)}")
    return removed


def _clone(repo: str, ref: str, dest: Path) -> None:
    if dest.is_symlink() or dest.exists():
        util.remove_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    util.log(f"adding {dest.name} ({repo} @ {ref})")
    util.clone(repo, dest, ref=ref, token=None, depth=1)


def _add_helloworld(tree: Path) -> Path:
    new_dir = Path(tree) / "package" / "new"
    new_dir.mkdir(parents=True, exist_ok=True)
    helloworld = new_dir / "helloworld"
    _clone(HELLOWORLD_REPO, HELLOWORLD_REF, helloworld)

    # autocore is a vendor helper for the prebuilt MTK images; the rebase tree
    # builds its own and a duplicate only slows the scan down.
    util.remove_path(helloworld / "autocore")

    # Upstream now carries openlist2 / luci-app-openlist2 at the top level;
    # the dedicated sbwml clone below provides both, and a second copy inside
    # package/new would trip the buildroot's duplicate-package check.
    util.remove_path(helloworld / "openlist2")
    util.remove_path(helloworld / "luci-app-openlist2")
    return helloworld


def _replace_feed_dir(tree: Path, relative: Path, repo: str, ref: str) -> Path:
    dest = Path(tree) / relative
    _clone(repo, ref, dest)
    return dest


def _ensure_feed_links(tree: Path) -> None:
    """Keep the package/feeds links valid after a feed directory is replaced.

    ``scripts/feeds install`` (run before this step) already created these
    links. Replacing the directory behind a link keeps it valid, but a package
    that did not exist in the feed index would otherwise never be scanned.
    """
    links = {
        Path("package/feeds/packages/golang"): Path("feeds/packages/lang/golang"),
        Path("package/feeds/packages/rust"): Path("feeds/packages/lang/rust"),
        Path("package/feeds/luci/luci-app-dockerman"): Path("feeds/luci/applications/luci-app-dockerman"),
    }
    for name in DOCKER_REPOS:
        links[Path("package/feeds/packages") / name] = Path("feeds/packages/utils") / name
    for link, target in links.items():
        link_path = Path(tree) / link
        target_path = Path(tree) / target
        if not target_path.exists() or link_path.is_symlink() or link_path.exists():
            continue
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(os.path.relpath(target_path, link_path.parent))


def add_external_packages(tree: Path, *, with_ota: bool = True) -> None:
    """Clone the package set used by the device config into the source tree.

    ``with_ota`` is False for source trees whose profile builds a plain image
    without ``luci-app-ota``; the clone is skipped so nothing selects a package
    that is not there.
    """
    tree = Path(tree)
    new_dir = tree / "package" / "new"
    new_dir.mkdir(parents=True, exist_ok=True)

    helloworld = _add_helloworld(tree)

    _replace_feed_dir(tree, Path("feeds/packages/lang/golang"), GOLANG_REPO, GOLANG_REF)
    _replace_feed_dir(tree, Path("feeds/packages/lang/rust"), RUST_REPO, RUST_REF)

    _replace_feed_dir(
        tree,
        Path("feeds/luci/applications/luci-app-dockerman"),
        DOCKERMAN_REPO,
        DOCKERMAN_REF,
    )
    for name, (repo, ref) in DOCKER_REPOS.items():
        _replace_feed_dir(tree, Path("feeds/packages/utils") / name, repo, ref)

    if with_ota:
        add_ota_app(tree)
    else:
        util.log("OTA app not requested for this profile; package/new/luci-app-ota stays empty")
    add_openlist(tree)

    _validate_layout(new_dir, helloworld, with_ota=with_ota)
    # The feeds were installed before this step; prune what package/new owns so
    # the build cannot see a duplicate. Links for the replaced feed directories
    # are created here because no second `feeds install` runs.
    prune_feed_conflicts(tree)
    _ensure_feed_links(tree)


def add_ota_app(tree: Path) -> Path:
    """Clone ``luci-app-ota`` into ``package/new``."""
    dest = Path(tree) / "package" / "new" / "luci-app-ota"
    _clone(OTA_REPO, OTA_REF, dest)
    if not (dest / "Makefile").exists():
        raise util.BuildError(f"luci-app-ota checkout is missing a Makefile: {dest}")
    if not (dest / "root" / "usr" / "bin" / "ota").exists():
        raise util.BuildError(f"luci-app-ota checkout is incomplete: {dest}")
    util.log("OTA app added: package/new/luci-app-ota")
    return dest


def add_openlist(tree: Path) -> Path:
    """Clone OpenList into ``package/new/openlist``.

    The checkout holds ``openlist2`` (the Go service) and ``luci-app-openlist2``
    (its front end) as siblings, so a single clone feeds both Makefiles.
    """
    dest = Path(tree) / "package" / "new" / "openlist"
    _clone(OPENLIST_REPO, OPENLIST_REF, dest)
    for name in ("openlist2", "luci-app-openlist2"):
        if not (dest / name / "Makefile").exists():
            raise util.BuildError(f"openlist checkout is missing {name}/Makefile: {dest}")
    util.log("OpenList added: package/new/openlist (openlist2 + luci-app-openlist2)")
    return dest


def _validate_layout(new_dir: Path, helloworld: Path, *, with_ota: bool = True) -> None:
    # Upstream renamed luci-app-argon-config to luci-app-aurora-config and the
    # latter only drives its own Aurora theme, so the Argon-based image no
    # longer expects a theme config app here.
    checks = {
        "package/new/helloworld/luci-theme-argon/Makefile": helloworld / "luci-theme-argon" / "Makefile",
        "package/new/helloworld/v2ray-geodata/Makefile": helloworld / "v2ray-geodata" / "Makefile",
        "package/new/openlist/openlist2/Makefile": new_dir / "openlist" / "openlist2" / "Makefile",
        "package/new/openlist/luci-app-openlist2/Makefile": (
            new_dir / "openlist" / "luci-app-openlist2" / "Makefile"
        ),
    }
    if with_ota:
        checks["package/new/luci-app-ota/Makefile"] = new_dir / "luci-app-ota" / "Makefile"
    missing = [name for name, path in checks.items() if not path.exists()]
    if missing:
        raise util.BuildError("unexpected repository layout, missing:\n  - " + "\n  - ".join(missing))
