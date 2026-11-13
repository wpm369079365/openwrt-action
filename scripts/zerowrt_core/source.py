"""Source tree handling: clone the MT798x tree, update feeds and verify them.

The feed layout is deliberately not hard-coded beyond the two names the build
cannot work without (``luci`` and ``packages``): feeds move packages between
directories, and a wrong path should fail with a readable message instead of a
nine-hour compile.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import packages, profiles, state, util
from .settings import BuildSettings


def _verify_tree(tree: Path) -> None:
    tree = Path(tree)
    if not (tree / "rules.mk").exists() or not (tree / "include" / "version.mk").exists():
        raise util.BuildError(
            f"{tree} does not look like an OpenWrt/ImmortalWrt tree "
            "(rules.mk and include/version.mk are missing)"
        )


def clone_source(
    tree: Path,
    *,
    device: str,
    ref: str | None = None,
    repo: str | None = None,
) -> dict:
    """Clone the source tree that *device* is built from and record its info."""
    profile = profiles.require(device)
    source = profiles.source(profile)
    repo = (repo or os.environ.get("ZEROWRT_SOURCE_REPO") or source.repo).strip()
    ref = (ref or os.environ.get("ZEROWRT_SOURCE_REF") or source.ref).strip()

    util.header(f"Cloning {source.label}")
    util.log(f"repository: {repo}")
    util.log(f"reference: {ref}")
    sha = util.clone(repo, Path(tree), ref=ref, token=None, depth=1)
    _verify_tree(Path(tree))
    util.log(f"checked out commit {sha[:12]}")

    data = {
        "dist": "ZeroWrt",
        "profile": profile.name,
        "source": profile.source,
        "source_label": source.label,
        "source_repo": repo,
        "source_ref": ref,
        "source_commit": sha,
        "board": profile.board,
        "subtarget": profile.subtarget,
        "target": f"{profile.board}/{profile.subtarget}",
        "device": profile.device,
        "dt_model": profile.dt_model,
        "device_vendor": profile.vendor,
        "device_model": profile.model,
        "layout": profile.layout,
        "build_date": int(time.time()),
        "version": "",
    }
    data["version"] = state.version_for(data["build_date"])
    state.write(Path(tree), data)
    return data


def update_feeds(tree: Path) -> None:
    """Fetch every feed; the feed sources are needed by the customisation step."""
    tree = Path(tree)
    feeds = tree / "scripts" / "feeds"
    if not feeds.exists():
        raise util.BuildError(f"feeds script not found: {feeds}")

    util.header("Updating OpenWrt feeds")
    code, _ = util.run(
        [str(feeds), "update", "-a"],
        cwd=tree,
        check=False,
        log_path=tree / "feeds-update.log",
    )
    missing = [name for name in ("luci", "packages") if not (tree / "feeds" / name).exists()]
    if missing:
        tail = ""
        log_path = tree / "feeds-update.log"
        if log_path.exists():
            tail = "\n".join(util.read_text(log_path).splitlines()[-40:])
        raise util.BuildError(
            "feed update did not provide: " + ", ".join(missing) + "\n" + tail
        )
    if code != 0:
        util.warn("feeds update reported an error, but luci/packages are present; continuing")


def install_feeds(tree: Path) -> None:
    """Link the feeds into ``package/feeds`` and drop packages we replace."""
    tree = Path(tree)
    feeds = tree / "scripts" / "feeds"
    if not feeds.exists():
        raise util.BuildError(f"feeds script not found: {feeds}")

    util.header("Installing OpenWrt feeds")
    util.run(
        [str(feeds), "install", "-a"],
        cwd=tree,
        check=False,
        log_path=tree / "feeds-install.log",
    )

    # package/new is cloned before this step, so the feed copies of those
    # packages have to disappear before the buildroot scans the tree.
    packages.prune_feed_conflicts(tree)

    required = {
        "luci-compat": tree / "package" / "feeds" / "luci" / "luci-compat",
        "lyaml": tree / "package" / "feeds" / "packages" / "lyaml",
        "ttyd": tree / "package" / "feeds" / "packages" / "ttyd",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        tail = ""
        log_path = tree / "feeds-install.log"
        if log_path.exists():
            tail = "\n".join(util.read_text(log_path).splitlines()[-40:])
        raise util.BuildError(
            "feeds install did not provide: " + ", ".join(missing) + "\n" + tail
        )
    util.log("feeds installed")


def ensure_patched_luci(tree: Path) -> None:
    """Fail early when the LuCI feed is not the tree the patches target."""
    tree = Path(tree)
    candidates = [
        tree / "feeds" / "luci" / "modules" / "luci-mod-status",
        tree / "feeds" / "luci" / "applications" / "luci-app-firewall",
    ]
    missing = [str(path.relative_to(tree)) for path in candidates if not path.exists()]
    if missing:
        raise util.BuildError("unexpected LuCI feed layout, missing: " + ", ".join(missing))


def settings(tree: Path) -> BuildSettings:
    """Convenience helper for tests and commands."""
    return BuildSettings.from_environment()
