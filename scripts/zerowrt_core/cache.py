"""Toolchain cache stored in a GitHub release.

Compiling the cross toolchain takes most of the first build's wall clock. The
cache is the same layout sbwml uses: a ``toolchain_<libc>_<arch>_gcc-<major>.tar.zst``
asset attached to the ``toolchain`` release and restored into the source tree
before ``make download``.

The cache is optional. A missing release or asset only produces a warning for
``restore`` so a first build can still compile the toolchain normally.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import util

DEFAULT_RELEASE = "toolchain"
TOOLCHAIN_RE = re.compile(
    r"^toolchain-(?P<arch>.+)_gcc-(?P<gcc>[0-9]+)\.[^_]+_(?P<libc>[^_]+)$"
)


def asset_name(staging_dir: Path) -> str:
    """Derive ``toolchain_<libc>_<arch>_gcc-<major>.tar.zst`` from staging_dir."""
    candidates = sorted(Path(staging_dir).glob("toolchain-*"))
    if not candidates:
        raise util.BuildError(f"no toolchain-* directory in {staging_dir}")
    directory = candidates[0].name
    match = TOOLCHAIN_RE.match(directory)
    if not match:
        raise util.BuildError(f"unexpected toolchain directory name: {directory}")
    return f"toolchain_{match.group('libc')}_{match.group('arch')}_gcc-{match.group('gcc')}.tar.zst"


def _gh() -> str:
    binary = util.which("gh")
    if binary is None:
        raise util.BuildError("the GitHub CLI (gh) is required for the toolchain cache")
    return binary


def _touch_tree(root: Path) -> None:
    if not root.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                os.utime(Path(dirpath) / name, None)
            except OSError:
                continue


def _expected_gcc_major(tree: Path) -> str:
    """GCC major selected in .config, used to reject a stale toolchain cache."""
    config = Path(tree) / ".config"
    if not config.exists():
        return ""
    text = util.read_text(config)
    match = re.search(r'^CONFIG_GCC_VERSION="?(\d+)\.', text, re.M)
    if match:
        return match.group(1)
    match = re.search(r"^CONFIG_GCC_VERSION_(\d+)=y", text, re.M)
    return match.group(1) if match else ""


def restore(tree: Path, *, repo: str, release: str = DEFAULT_RELEASE, optional: bool = True) -> Path | None:
    tree = Path(tree)
    try:
        gh = _gh()
    except util.BuildError:
        if optional:
            util.warn("gh is not installed; skipping the toolchain cache")
            return None
        raise

    code, output = util.run(
        [gh, "release", "view", release, "--repo", repo, "--json", "assets"],
        capture=True,
        quiet=True,
        check=False,
    )
    if code != 0:
        message = f"no usable toolchain cache ({release}): {output.strip()[:300]}"
        if optional:
            util.warn(message)
            return None
        raise util.BuildError(message)

    import json

    try:
        assets = json.loads(output).get("assets", [])
    except json.JSONDecodeError as exc:
        raise util.BuildError(f"cannot parse gh release output: {exc}") from exc
    matching = [
        asset
        for asset in assets
        if str(asset.get("name", "")).startswith("toolchain_")
        and str(asset.get("name", "")).endswith(".tar.zst")
    ]
    expected_gcc = _expected_gcc_major(tree)
    if expected_gcc:
        matching = [
            asset for asset in matching if f"_gcc-{expected_gcc}.tar.zst" in str(asset.get("name", ""))
        ]
    if not matching:
        message = (
            f"release {release!r} has no toolchain_*.tar.zst asset"
            + (f" for GCC {expected_gcc}" if expected_gcc else "")
        )
        if optional:
            util.warn(message)
            return None
        raise util.BuildError(message)
    matching.sort(key=lambda asset: (asset.get("createdAt", ""), asset.get("name", "")))
    asset = str(matching[-1]["name"])
    util.header(f"Restoring toolchain cache {asset}")

    cache_dir = tree / "toolchain-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / asset
    util.run(
        [gh, "release", "download", release, "--repo", repo, "--pattern", asset, "--output", str(archive), "--clobber"],
        display=f"gh release download {release} --pattern {asset}",
    )
    try:
        util.run(["tar", "-I", "zstd", "-xf", str(archive)], cwd=tree)
    finally:
        archive.unlink(missing_ok=True)

    _touch_tree(tree / "staging_dir")
    _touch_tree(tree / "tmp")
    util.log(f"toolchain cache restored ({asset})")
    return tree / "staging_dir"


def save(tree: Path, *, repo: str, release: str = DEFAULT_RELEASE) -> Path:
    tree = Path(tree)
    gh = _gh()
    name = asset_name(tree / "staging_dir")
    cache_dir = tree / "toolchain-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / name
    if archive.exists():
        archive.unlink()

    util.header(f"Packaging toolchain cache {name}")
    members = [str(path) for path in ("./build_dir", "./dl", "./staging_dir", "./tmp") if (tree / path[2:]).exists()]
    if not members:
        raise util.BuildError("no build_dir/dl/staging_dir/tmp to package")
    util.run(["tar", "-I", "zstd -19 -T0", "-cf", str(archive), *members], cwd=tree)
    util.log(f"toolchain cache: {archive.name} ({archive.stat().st_size // (1024**2)} MiB)")

    code, _ = util.run(
        [gh, "release", "view", release, "--repo", repo],
        capture=True,
        quiet=True,
        check=False,
    )
    if code != 0:
        util.run(
            [
                gh,
                "release",
                "create",
                release,
                "--repo",
                repo,
                "--title",
                "Toolchain_Cache",
                "--notes",
                "Cross toolchain cache restored by the ZeroWrt build workflow.",
            ]
        )
    util.run(
        [gh, "release", "upload", release, str(archive), "--repo", repo, "--clobber"],
        display=f"gh release upload {release} {name}",
    )
    util.log(f"toolchain cache published to {repo}@{release}")
    return archive


def clean(tree: Path) -> None:
    shutil.rmtree(Path(tree) / "toolchain-cache", ignore_errors=True)
