"""Apply the LuCI, toolchain and package patches that the image depends on.

The five LuCI patches are refreshed against the ``openwrt-25.12`` feed (their
``index`` lines match the pinned tree). The GCC-16 toolchain patch is applied to
the source root, while the two small package patches are copied into the
package patch directories so the buildroot applies them after unpacking.

Applying a patch twice is a no-op: the reverse check catches a tree that was
already patched, which matters when a workflow is re-run on a cached tree.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import profiles, util

SCRIPTS_DIR = Path(__file__).resolve().parents[1]

LUCI_PATCHES = [
    "0001-luci-mod-status-add-help-and-feedback-links.patch",
    "0002-luci-base-add-Chinese-translations-for-support-links.patch",
    "0003-luci-app-firewall-remove-flow-offloading-settings.patch",
    "0004-luci-mod-system-add-modal-overlay-dialog-to-reboot.patch",
    "0005-luci-mod-status-add-network-speed-monitor.patch",
]
SOURCE_PATCHES = [
    "0006-toolchain-gcc-add-support-for-GCC-16.patch",
]
ELFUTILS_PATCH = "900-fix-gcc16-null-dereference-with-lto.patch"
LIBWEBSOCKETS_PATCH = "900-fix-build-for-gcc-16.patch"


def _patch_path(name: str, scripts: Path) -> Path:
    path = Path(scripts) / name
    if not path.is_file():
        raise util.BuildError(f"patch file not found: {path}")
    return path


def plan(profile: profiles.DeviceProfile | None) -> dict[str, list[str] | bool]:
    """Which patch groups belong to the tree behind *profile*.

    The five LuCI patches are refreshed against the 25.12 feed and the three
    toolchain patches only make sense with GCC 16. On the 24.10 tree both groups
    are skipped instead of failing the build, because a patch that cannot apply
    would abort it.
    """
    if profile is None:
        return {
            "luci": list(LUCI_PATCHES),
            "source": list(SOURCE_PATCHES),
            "gcc16": True,
        }
    source = profiles.source(profile)
    if source.gcc16:
        return {
            "luci": list(LUCI_PATCHES) if source.luci_patches else [],
            "source": list(SOURCE_PATCHES),
            "gcc16": True,
        }
    return {"luci": [], "source": [], "gcc16": False}


def _git(directory: Path) -> bool:
    return util.is_git_repo(Path(directory))


def apply_patch(directory: Path, patch: Path, *, strip: int = 1) -> str:
    """Apply *patch* in *directory*; return ``applied`` or ``already``."""
    directory = Path(directory)
    patch = Path(patch)
    if not directory.exists():
        raise util.BuildError(f"patch target does not exist: {directory}")

    if _git(directory):
        code, output = util.run(
            ["git", "-C", str(directory), "apply", "--check", f"-p{strip}", str(patch)],
            capture=True,
            quiet=True,
            check=False,
        )
        if code != 0:
            code, _ = util.run(
                ["git", "-C", str(directory), "apply", "--reverse", "--check", f"-p{strip}", str(patch)],
                capture=True,
                quiet=True,
                check=False,
            )
            if code == 0:
                util.log(f"already applied: {patch.name}")
                return "already"
            raise util.BuildError(f"{patch.name} does not apply in {directory}:\n{output[-2000:]}")
        util.run(["git", "-C", str(directory), "apply", f"-p{strip}", str(patch)], quiet=True)
        util.log(f"applied {patch.name}")
        return "applied"

    dry = util.run(
        ["patch", f"-p{strip}", "--dry-run", "--forward", "-i", str(patch)],
        cwd=directory,
        capture=True,
        check=False,
    )[1]
    if "Reversed (or previously applied) patch detected" in dry:
        util.log(f"already applied: {patch.name}")
        return "already"
    util.run(["patch", f"-p{strip}", "--forward", "-i", str(patch)], cwd=directory, check=True)
    util.log(f"applied {patch.name}")
    return "applied"


def _copy_patch(source: Path, destination: Path) -> None:
    package = destination.parent.parent
    if not package.exists():
        raise util.BuildError(f"package directory is missing for {destination}: {package}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() == source.read_bytes():
        util.log(f"package patch already present: {destination}")
        return
    shutil.copyfile(source, destination)
    util.log(f"installed package patch: {destination}")


def _patch_xl2tpd(tree: Path) -> None:
    makefile = tree / "feeds" / "packages" / "net" / "xl2tpd" / "Makefile"
    if not makefile.exists():
        util.warn("xl2tpd Makefile not found; skipping the gnu17 workaround")
        return
    text = util.read_text(makefile)
    if "TARGET_CFLAGS += -std=gnu17" in text:
        util.log("xl2tpd already builds with -std=gnu17")
        return
    if "ifneq (0,0)" in text:
        text = text.replace("ifneq (0,0)", "TARGET_CFLAGS += -std=gnu17\n\nifneq (0,0)", 1)
    else:
        text = text.replace(
            "include $(INCLUDE_DIR)/package.mk",
            "include $(INCLUDE_DIR)/package.mk\n\nTARGET_CFLAGS += -std=gnu17",
            1,
        )
    util.write_text(makefile, text)
    util.log("xl2tpd: TARGET_CFLAGS += -std=gnu17")


def _patch_bash(tree: Path) -> None:
    makefile = tree / "feeds" / "packages" / "utils" / "bash" / "Makefile"
    if not makefile.exists():
        util.warn("bash Makefile not found; skipping the no-lto flag")
        return
    text = util.read_text(makefile)
    if re.search(r"^PKG_BUILD_FLAGS\s*:?=.*no-lto", text, re.M):
        util.log("bash already builds with no-lto")
        return
    text, count = re.subn(
        r"^(PKG_INSTALL\s*:?=)",
        "PKG_BUILD_FLAGS:=no-lto\n\\1",
        text,
        count=1,
        flags=re.M,
    )
    if count != 1:
        util.warn("bash Makefile: PKG_INSTALL not found; no-lto flag not added")
        return
    util.write_text(makefile, text)
    util.log("bash: PKG_BUILD_FLAGS:=no-lto")


def apply_all(tree: Path, *, scripts: Path | None = None, profile: profiles.DeviceProfile | None = None) -> None:
    tree = Path(tree)
    scripts = Path(scripts) if scripts else SCRIPTS_DIR
    groups = plan(profile)

    if groups["luci"]:
        luci = tree / "feeds" / "luci"
        if not luci.exists():
            raise util.BuildError(f"LuCI feed is missing: {luci}")
        util.header("Applying LuCI patches")
        for name in groups["luci"]:
            apply_patch(luci, _patch_path(name, scripts))
    else:
        util.header("Skipping LuCI patches")
        util.log("this tree's LuCI feed is not the one the patches target")

    if groups["source"]:
        util.header("Applying source patches")
        for name in groups["source"]:
            apply_patch(tree, _patch_path(name, scripts))
    else:
        util.header("Skipping toolchain patches")
        util.log("this tree does not use the GCC 16 toolchain")

    util.header("Installing package patches")
    if groups["gcc16"]:
        _copy_patch(
            _patch_path(ELFUTILS_PATCH, scripts),
            tree / "package" / "libs" / "elfutils" / "patches" / ELFUTILS_PATCH,
        )
        _copy_patch(
            _patch_path(LIBWEBSOCKETS_PATCH, scripts),
            tree / "feeds" / "packages" / "libs" / "libwebsockets" / "patches" / LIBWEBSOCKETS_PATCH,
        )
        _patch_xl2tpd(tree)
        _patch_bash(tree)
    else:
        util.log("no package patches for this tree")

    verify(tree, scripts=scripts, profile=profile)


def verify(tree: Path, *, scripts: Path | None = None, profile: profiles.DeviceProfile | None = None) -> None:
    """Check that every patch selected by :func:`plan` is present in the tree."""
    tree = Path(tree)
    scripts = Path(scripts) if scripts else SCRIPTS_DIR
    groups = plan(profile)
    util.header("Verifying patches")
    problems: list[str] = []

    luci = tree / "feeds" / "luci"
    for name in groups["luci"]:
        code, _ = util.run(
            ["git", "-C", str(luci), "apply", "--reverse", "--check", str(Path(scripts) / name)],
            capture=True,
            quiet=True,
            check=False,
        )
        if code != 0:
            problems.append(f"not applied in feeds/luci: {name}")

    for name in groups["source"]:
        code, _ = util.run(
            ["git", "-C", str(tree), "apply", "--reverse", "--check", str(Path(scripts) / name)],
            capture=True,
            quiet=True,
            check=False,
        )
        if code != 0:
            problems.append(f"not applied in the source tree: {name}")

    if groups["gcc16"]:
        for relative in (
            f"package/libs/elfutils/patches/{ELFUTILS_PATCH}",
            f"feeds/packages/libs/libwebsockets/patches/{LIBWEBSOCKETS_PATCH}",
        ):
            if not (tree / relative).is_file():
                problems.append(f"missing package patch: {relative}")

        xl2tpd = tree / "feeds" / "packages" / "net" / "xl2tpd" / "Makefile"
        if xl2tpd.exists() and "TARGET_CFLAGS += -std=gnu17" not in util.read_text(xl2tpd):
            problems.append("xl2tpd does not build with -std=gnu17")

        bash = tree / "feeds" / "packages" / "utils" / "bash" / "Makefile"
        if bash.exists() and "no-lto" not in util.read_text(bash):
            problems.append("bash does not build with no-lto")

    if problems:
        raise util.BuildError("patch verification failed:\n  - " + "\n  - ".join(problems))
    util.log("patches verified")
