"""Runner preparation: disk merging, build dependencies and LLVM.

These helpers mirror the behaviour of the composite actions used by other
OpenWrt builders (free-disk, openwrt-build-setup, install-llvm) but are
implemented locally with Python so the workflow has no third-party action
dependency besides the official GitHub ones.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path

try:  # POSIX only; the helpers are exercised on Linux runners.
    import pwd
except ImportError:  # pragma: no cover - Windows development machines
    pwd = None

from . import util

# Directories shipped with GitHub's Ubuntu images that an OpenWrt build never
# needs. Removing them frees several gigabytes on the root filesystem.
HEAVY_DIRECTORIES = [
    "/usr/local/lib/android",
    "/usr/lib/google-cloud-sdk",
    "/usr/lib/jvm",
    "/usr/lib/clang-16",
    "/usr/lib/clang-17",
    "/usr/lib/clang-18",
    "/usr/lib/clang-19",
    "/usr/lib/llvm-16",
    "/usr/lib/llvm-17",
    "/usr/lib/llvm-18",
    "/usr/lib/llvm-19",
    "/usr/local/aws",
    "/usr/local/aws-cli",
    "/usr/local/julia",
    "/usr/local/share/chromium",
    "/usr/local/share/powershell",
    "/usr/local/share/vcpkg",
    "/usr/share/dotnet",
    "/usr/share/swift",
    "/opt/az",
    "/opt/microsoft",
    "/opt/pipx",
]

# Versioned toolchain directories that are removed with a glob.
HEAVY_GLOBS = [
    "/usr/local/aws*",
    "/usr/local/julia*",
]

SWAPFILE_CANDIDATES = ["/mnt/swapfile", "/swapfile"]


def _run_user() -> str:
    """Name of the unprivileged account that runs the build."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    if pwd is not None and hasattr(os, "getuid"):
        try:
            name = pwd.getpwuid(os.getuid()).pw_name
            if name != "root":
                return name
        except KeyError:
            pass
    return "runner"


def show_system() -> None:
    util.header("Runner resources")
    util.run(["free", "-h"], check=False)
    util.run(["df", "-h"], check=False)


def _df_field(path: str, field: int) -> int:
    code, output = util.sudo_run(["df", "-P", "-B1", path], capture=True, quiet=True, check=False)
    if code != 0:
        raise util.BuildError(f"unable to stat filesystem for {path}")
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise util.BuildError(f"unexpected df output for {path}: {output!r}")
    parts = lines[-1].split()
    try:
        return int(parts[field])
    except (IndexError, ValueError) as exc:
        raise util.BuildError(f"unexpected df output for {path}: {output!r}") from exc


def _device(path: str) -> str:
    return util.sudo_output(["df", "-P", path]).splitlines()[-1].split()[0]


GIB = 1024**3


def _available_gb(path: str) -> float:
    try:
        return _df_field(path, 3) / GIB
    except util.BuildError:
        return 0.0


def _swap_total_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0.0
    for line in util.read_text(meminfo).splitlines():
        if line.startswith("SwapTotal:"):
            try:
                return int(line.split()[1]) / (1024**2)
            except (IndexError, ValueError):
                return 0.0
    return 0.0


def _mnt_is_separate() -> bool:
    if not Path("/mnt").is_dir():
        return False
    try:
        return _device("/mnt") != _device("/")
    except util.BuildError:
        return False


def _prepare_build_dir(build_dir: Path) -> None:
    user = _run_user()
    util.sudo_run(["mkdir", "-p", str(build_dir), str(build_dir / "tmp")], check=False)
    util.sudo_run(["chown", "-R", f"{user}:{user}", str(build_dir)], check=False)


def _delete_swap() -> None:
    util.sudo_run(["swapoff", "-a"], check=False)
    for swapfile in SWAPFILE_CANDIDATES:
        util.sudo_run(["rm", "-f", swapfile], check=False)


def _create_swap(swap_gb: int) -> None:
    if swap_gb <= 0:
        util.log("swap creation disabled")
        return
    target = "/mnt/swapfile" if _mnt_is_separate() else "/swapfile"
    util.header(f"Creating {swap_gb} GiB swap at {target}")
    util.sudo_run(["rm", "-f", target], check=False)
    # fallocate is much faster than dd; fall back when the filesystem
    # (or the image) does not support it.
    code, _ = util.sudo_run(["fallocate", "-l", f"{swap_gb}G", target], check=False, quiet=True)
    if code != 0:
        util.sudo_run(["dd", "if=/dev/zero", f"of={target}", "bs=1M", f"count={swap_gb * 1024}", "status=none"])
    util.sudo_run(["chmod", "600", target])
    util.sudo_run(["mkswap", target])
    util.sudo_run(["swapon", target])


def _ensure_disk_tools() -> None:
    if shutil.which("pvcreate") and shutil.which("mkfs.xfs"):
        return
    util.log("installing lvm2 and xfsprogs")
    util.sudo_run(["apt-get", "update", "-qq"], check=False)
    util.sudo_run(
        ["apt-get", "install", "-y", "-qq", "lvm2", "xfsprogs"],
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )


def _fallback_build_dir(build_dir: Path) -> None:
    """Use /mnt when the LVM merge is not possible."""
    build_dir.mkdir(parents=True, exist_ok=True)
    util.warn(f"falling back to a non-merged build directory at {build_dir}")
    if Path("/mnt").is_dir() and _device("/mnt") != _device("/"):
        code, _ = util.sudo_run(["mount", "--bind", "/mnt", str(build_dir)], check=False, quiet=True)
        if code == 0:
            util.log(f"bind mounted /mnt on {build_dir}")
        else:
            util.warn("/mnt could not be bind mounted; building on the root filesystem")


def merge_disks(build_dir: Path, *, reserve_gb: int = 2) -> None:
    """Grow the build space by joining the root and /mnt disks with LVM."""
    build_dir = Path(build_dir)
    root_dev = _device("/")
    try:
        mnt_dev = _device("/mnt")
    except util.BuildError:
        mnt_dev = root_dev

    if root_dev == mnt_dev:
        util.warn("/ and /mnt share the same block device; nothing to merge")
        util.sudo_run(["mkdir", "-p", str(build_dir)], check=False)
        util.sudo_run(["chown", "-R", f"{_run_user()}:{_run_user()}", str(build_dir)], check=False)
        return

    util.header("Merging the runner disks with LVM")
    _ensure_disk_tools()

    mnt_avail = _df_field("/mnt", 3)
    root_avail = _df_field("/", 3)
    # Leave a little room on each filesystem for metadata and the images
    # themselves; the LVM volume gets everything else.
    mnt_size = (mnt_avail - 512 * 1024 * 1024) // (1024 * 1024)
    root_size = (root_avail - max(reserve_gb, 1) * 1024 * 1024 * 1024) // (1024 * 1024)
    if mnt_size < 1024 or root_size < 1024:
        util.warn("not enough free space to merge the disks")
        _fallback_build_dir(build_dir)
        return

    mnt_img = Path("/mnt/mnt.img")
    root_img = Path("/root.img")
    loops: list[str] = []
    vg_name = "zerowrt"
    lv_path = f"/dev/mapper/{vg_name}-build"

    try:
        util.sudo_run(["rm", "-f", str(mnt_img), str(root_img)], check=False)
        util.log(f"reserving {mnt_size} MiB on /mnt and {root_size} MiB on /")
        util.sudo_run(["truncate", "-s", f"{mnt_size}M", str(mnt_img)])
        util.sudo_run(["truncate", "-s", f"{root_size}M", str(root_img)])

        loop_mnt = util.sudo_output(["losetup", "-f", "--show", str(mnt_img)])
        loops.append(loop_mnt)
        loop_root = util.sudo_output(["losetup", "-f", "--show", str(root_img)])
        loops.append(loop_root)
        util.log(f"loop devices: {loop_mnt}, {loop_root}")

        util.sudo_run(["pvcreate", "-y", loop_mnt, loop_root])
        util.sudo_run(["vgcreate", "-y", vg_name, loop_mnt, loop_root])
        util.sudo_run(["lvcreate", "-y", "-n", "build", "-l", "100%FREE", vg_name])
        util.sudo_run(["mkfs.xfs", "-f", lv_path])
        util.sudo_run(["mkdir", "-p", str(build_dir)])
        util.sudo_run(["mount", lv_path, str(build_dir)])
    except Exception as exc:  # noqa: BLE001 - fall back to any usable space
        util.warn(f"disk merge failed: {exc}")
        util.sudo_run(["vgremove", "-f", vg_name], check=False, quiet=True)
        for loop in loops:
            util.sudo_run(["losetup", "-d", loop], check=False, quiet=True)
        util.sudo_run(["rm", "-f", str(mnt_img), str(root_img)], check=False, quiet=True)
        _fallback_build_dir(build_dir)
        return

    util.sudo_run(["chown", "-R", f"{_run_user()}:{_run_user()}", str(build_dir)])
    util.sudo_run(["mkdir", "-p", str(build_dir / "tmp")])
    util.sudo_run(["chown", "-R", f"{_run_user()}:{_run_user()}", str(build_dir / "tmp")])
    util.log(f"build space ready at {build_dir}")


def free_disk(
    build_dir: str = "/builder",
    *,
    reserve_gb: int = 2,
    swap_size_gb: int = 4,
    min_free_gb: float = 50.0,
) -> None:
    """Make sure the runner has enough disk and swap for a full build.

    When the runner has a separate /mnt disk the two filesystems are merged
    (like sbwml/actions@free-disk) so the build gets one large volume. When
    /mnt is not separate and the root disk is already large enough, the
    expensive removal step is skipped.
    """
    build_dir_path = Path(build_dir)
    show_system()

    probe = str(build_dir_path) if build_dir_path.exists() else "/"
    free_gb = _available_gb(probe)
    swap_gb = _swap_total_gb()
    mnt_separate = _mnt_is_separate()
    if free_gb >= min_free_gb and not mnt_separate:
        if swap_gb < swap_size_gb:
            util.log(f"{free_gb:.1f} GiB free is enough; adjusting swap from {swap_gb:.1f} to {swap_size_gb} GiB")
            _delete_swap()
            _create_swap(swap_size_gb)
        else:
            util.log(f"{free_gb:.1f} GiB free and {swap_gb:.1f} GiB swap available; skipping cleanup and disk merge")
        _prepare_build_dir(build_dir_path)
        _report_build_space(build_dir_path)
        return

    util.header("Removing unneeded runner components")
    for directory in HEAVY_DIRECTORIES:
        if Path(directory).exists():
            util.log(f"removing {directory}")
            util.sudo_run(["rm", "-rf", directory], check=False, quiet=True)
    for pattern in HEAVY_GLOBS:
        for matched in glob.glob(pattern):
            util.log(f"removing {matched}")
            util.sudo_run(["rm", "-rf", matched], check=False, quiet=True)
    # /opt is only used by preinstalled toolchains on GitHub runners.
    util.sudo_run(
        ["bash", "-c", "find /opt -mindepth 1 -maxdepth 1 ! -name '*runner*' -exec rm -rf {} +"],
        check=False,
        quiet=True,
    )

    if swap_gb < swap_size_gb:
        _delete_swap()
        _create_swap(swap_size_gb)
    else:
        util.log(f"existing {swap_gb:.1f} GiB swap is sufficient")

    if mnt_separate:
        merge_disks(build_dir_path, reserve_gb=reserve_gb)
    else:
        _prepare_build_dir(build_dir_path)

    util.sudo_run(["sysctl", "-w", "vm.drop_caches=3"], check=False, quiet=True)
    _report_build_space(build_dir_path)


def _report_build_space(build_dir: Path) -> None:
    util.header("Build space")
    util.run(["free", "-h"], check=False)
    util.run(["df", "-h", str(build_dir)], check=False)


APT_GROUPS = [
    [
        "build-essential",
        "binutils",
        "bison",
        "flex",
        "g++",
        "g++-multilib",
        "gawk",
        "gcc-multilib",
        "libc6-dev-i386",
        "lib32gcc-s1",
        "coreutils",
        "make",
        "pkg-config",
        "patch",
        "bc",
        "cpio",
        "rsync",
        "git",
        "subversion",
        "autoconf",
        "automake",
        "libtool",
        "cmake",
        "scons",
        "zstd",
        "swig",
        "gperf",
        "gettext",
        "texinfo",
        "xmlto",
        "asciidoc",
        "antlr3",
        "msmtp",
        "haveged",
        "jq",
        "time",
        "ccache",
    ],
    [
        "libncurses-dev",
        "libncurses5-dev",
        "libssl-dev",
        "libelf-dev",
        "zlib1g-dev",
        "libfuse-dev",
        "libglib2.0-dev",
        "libreadline-dev",
        "libgnutls28-dev",
        "gnutls-dev",
        "libplist-utils",
        "libpython3-dev",
        "libltdl-dev",
        "python3",
        "python3-pip",
        "python3-ply",
        "python3-pyelftools",
        "python3-pil",
        "device-tree-compiler",
    ],
    [
        "curl",
        "wget",
        "openssl",
        "xz-utils",
        "bzip2",
        "unzip",
        "zip",
        "file",
        "rename",
        "qemu-utils",
        "uglifyjs",
        "upx-ucl",
        "lvm2",
        "xfsprogs",
    ],
]


def _apt_install(packages: list[str]) -> None:
    if not packages:
        return
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    code, _ = util.sudo_run(["apt-get", "install", "-y", *packages], env=env, check=False)
    if code == 0:
        return
    util.warn("batch install failed, retrying package by package")
    for package in packages:
        code, _ = util.sudo_run(["apt-get", "install", "-y", package], env=env, check=False, quiet=True)
        if code != 0:
            util.warn(f"not installed (optional): {package}")


def install_docker() -> None:
    util.header("Installing Docker (for OpenWrt docker packages)")
    key = Path("/tmp/docker.gpg")
    util.download("https://download.docker.com/linux/ubuntu/gpg", key)
    util.sudo_run(["gpg", "--dearmor", "--yes", "-o", "/usr/share/keyrings/docker.gpg", str(key)])
    codename = "noble"
    os_release = Path("/etc/os-release")
    if os_release.exists():
        match = re.search(r'^VERSION_CODENAME="?([^"\n]+)"?', util.read_text(os_release), re.M)
        if match:
            codename = match.group(1)
    repo_line = (
        "deb [arch=amd64 signed-by=/usr/share/keyrings/docker.gpg] "
        f"https://download.docker.com/linux/ubuntu {codename} stable\n"
    )
    util.write_text(Path("/tmp/docker.list"), repo_line)
    util.sudo_run(["cp", "/tmp/docker.list", "/etc/apt/sources.list.d/docker.list"])
    util.sudo_run(["apt-get", "update", "-y"], check=False)
    _apt_install(
        [
            "containerd.io",
            "docker-buildx-plugin",
            "docker-ce",
            "docker-ce-cli",
            "docker-ce-rootless-extras",
            "docker-compose-plugin",
        ]
    )
    docker_init = Path("/usr/libexec/docker/docker-init")
    if docker_init.exists():
        util.sudo_run(["ln", "-sf", str(docker_init), "/usr/local/bin/docker-init"])


def install_deps(*, docker: bool = False) -> None:
    util.header("Installing OpenWrt build dependencies")
    util.sudo_run(["apt-get", "update", "-y"], check=False)
    for group in APT_GROUPS:
        _apt_install(group)
    if docker:
        install_docker()
    util.sudo_run(["apt-get", "clean"], check=False, quiet=True)
    util.log("build dependencies installed")


CLANG_VERSION = os.environ.get("ZEROWRT_CLANG_VERSION", "23.0.1")
CLANG_URL = (
    "https://github.com/sbwml/openwrt-llvm-toolchain/releases/download/"
    f"{CLANG_VERSION}/clang-{CLANG_VERSION}-x86_64-unknown-linux-gnu.tar.xz"
)


def install_llvm(*, version: str = CLANG_VERSION, url: str | None = None, strict: bool = False) -> None:
    util.header(f"Installing LLVM/Clang {version}")
    clang = Path("/opt/clang/bin/clang")
    if clang.exists():
        util.log(f"already installed: {clang}")
    else:
        archive = Path(f"/tmp/clang-{version}.tar.xz")
        try:
            util.download(url or CLANG_URL, archive, timeout=300)
            util.sudo_run(["mkdir", "-p", "/opt/clang"])
            util.sudo_run(["tar", "--strip-components=1", "-C", "/opt/clang", "-xf", str(archive)])
            archive.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            util.warn(f"LLVM installation skipped: {exc}")
            return

    code, output = util.run([str(clang), "--version"], capture=True, quiet=True, check=False)
    if code != 0:
        if strict:
            raise util.BuildError("clang --version failed")
        util.warn("clang --version failed; continuing without LLVM")
        return
    util.log(output.splitlines()[0] if output else "clang installed")

    # Persist the PATH change for the following workflow steps.
    github_path = os.environ.get("GITHUB_PATH")
    if github_path:
        with open(github_path, "a", encoding="utf-8") as handle:
            handle.write("/opt/clang/bin\n")
    else:
        util.warn("GITHUB_PATH is not set; add /opt/clang/bin to PATH manually")
    os.environ["PATH"] = f"/opt/clang/bin:{os.environ.get('PATH', '')}"
