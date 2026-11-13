"""Downloading, compiling and collecting the firmware.

The compile policy follows the ZeroWrt builder: one more job than the CPU
count, capped by available memory, ``IGNORE_ERRORS="n m"`` for the two optional
package classes, and a serial ``V=s`` retry when the parallel build fails. The
retry is what turns "failed to build" into a readable compiler error.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
from pathlib import Path

from . import state, util

GIB = 1024**3


def _available_memory_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0.0
    for line in util.read_text(meminfo).splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1]) / (1024**2)
            except (IndexError, ValueError):
                return 0.0
    return 0.0


def compile_jobs(requested: int | None = None) -> int:
    """Parallelism for ``make``: ``nproc + 1``, capped at 1.5 GiB per job."""
    base = max(1, int(requested or os.cpu_count() or 2))
    jobs = base + 1
    memory = _available_memory_gb()
    if memory > 0:
        jobs = min(jobs, max(1, int(memory // 1.5)))
    return max(1, jobs)


def download_sources(tree: Path, jobs: int | None = None) -> None:
    tree = Path(tree)
    jobs = max(1, int(jobs or os.cpu_count() or 2))
    util.header("Downloading source archives")
    log = tree / "download.log"
    code, _ = util.run(["make", f"-j{jobs}", "download"], cwd=tree, log_path=log, check=False)
    if code != 0:
        util.warn("parallel download failed; retrying serially")
        util.run(["make", "-j1", "download"], cwd=tree, log_path=log, check=True)


def compile_firmware(tree: Path, jobs: int | None = None) -> None:
    tree = Path(tree)
    cores = compile_jobs(jobs)
    log = tree / "build.log"

    util.header(f'Compiling ZeroWrt (make -j{cores} IGNORE_ERRORS="n m")')
    code, _ = util.run(
        ["make", f"-j{cores}", "IGNORE_ERRORS=n m"],
        cwd=tree,
        log_path=log,
        check=False,
    )
    if code == 0:
        util.log("firmware compiled")
        return

    # A parallel build only reports "failed to build"; rebuild serially with
    # V=s so the log carries the failing command and its output.
    util.warn(f"make exited with {code}; retrying serially with V=s")
    util.run(
        ["make", "-j1", "IGNORE_ERRORS=n m", "V=s"],
        cwd=tree,
        log_path=log,
        check=True,
    )
    util.log("firmware compiled after the verbose retry")


def _target_dir(tree: Path, info: dict) -> Path:
    targets = tree / "bin" / "targets"
    board = info.get("board", "")
    subtarget = info.get("subtarget", "")
    if board and subtarget and (targets / board / subtarget).is_dir():
        return targets / board / subtarget
    candidates = sorted(path for path in targets.glob("*/*") if path.is_dir())
    if not candidates:
        raise util.BuildError(f"no build output found under {targets}")
    if len(candidates) > 1:
        util.warn(f"several target directories found; using {candidates[0]}")
    return candidates[0]


def _collect_files(source_dir: Path, out_dir: Path) -> list[Path]:
    patterns = [
        "*.bin",
        "*.itb",
        "*.img",
        "*.img.gz",
        "*.tar",
        "*.manifest",
        "*.buildinfo",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(source_dir.glob(pattern)))
    files = [path for path in files if path.is_file()]

    collected: list[Path] = []
    for path in files:
        destination = out_dir / path.name
        if destination.exists():
            destination.unlink()
        shutil.copy2(path, destination)
        collected.append(destination)
        util.log(f"collected {destination.name}")
    return collected


def _packages_archive(tree: Path, out_dir: Path) -> Path | None:
    packages = tree / "bin" / "packages"
    if not packages.is_dir():
        return None
    suffix = ".apk" if any(packages.rglob("*.apk")) else ".ipk"
    members = [path for path in sorted(packages.rglob(f"*{suffix}")) if path.is_file()]
    if not members:
        return None
    destination = out_dir / "Packages.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for path in members:
            archive.add(path, arcname=f"packages/{path.relative_to(packages).as_posix()}")
    util.log(f"collected {destination.name} ({len(members)} package(s))")
    return destination


def _kernel_version(source_dir: Path) -> str:
    manifests = sorted(source_dir.glob("*.manifest"))
    for manifest in manifests:
        for line in util.read_text(manifest).splitlines():
            match = re.match(r"^kernel\s+-\s+(\S+)", line)
            if match:
                return match.group(1)
    return ""


def _build_info(info: dict, *, kernel: str) -> str:
    built = state.iso_for(state.build_date(info))
    device = str(info.get("device", ""))
    vendor = str(info.get("device_vendor", "")).strip()
    model = str(info.get("device_model", "")).strip()
    product = " ".join(part for part in (vendor, model) if part)
    device_line = f"device: {device}" + (f" ({product})" if product else "")
    lines = [
        f"{info.get('dist', 'ZeroWrt')} firmware",
        f"built: {built}",
        f"tree: {info.get('board', '')}/{info.get('subtarget', '')}",
        device_line,
        f"version: {info.get('version', '')}",
    ]
    for key, label in (
        ("artifact", "profile"),
        ("layout", "layout"),
        ("source_repo", "source repo"),
        ("source_ref", "source ref"),
        ("source_commit", "source commit"),
        ("config", "config"),
    ):
        value = info.get(key)
        if value:
            lines.append(f"{label}: {value}")
    if kernel:
        lines.append(f"kernel: {kernel}")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id:
        lines.append(f"workflow run: {run_id}")
    return "\n".join(lines) + "\n"


def collect(tree: Path, out_dir: Path) -> list[Path]:
    tree = Path(tree)
    out_dir = Path(out_dir)
    info = state.read(tree)
    source_dir = _target_dir(tree, info)

    util.header(f"Collecting artifacts from {source_dir.relative_to(tree)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    collected = _collect_files(source_dir, out_dir)

    config = tree / ".config"
    if config.exists():
        shutil.copyfile(config, out_dir / "build.config")
        collected.append(out_dir / "build.config")

    packages = _packages_archive(tree, out_dir)
    if packages is not None:
        collected.append(packages)

    kernel = _kernel_version(source_dir)
    build_info = out_dir / "BUILD_INFO.txt"
    util.write_text(build_info, _build_info(info, kernel=kernel))
    collected.append(build_info)

    checksum_file = out_dir / "SHA256SUMS"
    lines = [f"{util.sha256_file(path)}  {path.name}" for path in sorted(collected)]
    util.write_text(checksum_file, "\n".join(lines) + "\n")
    collected.append(checksum_file)

    has_image = any(
        path.suffix in {".bin", ".itb", ".img", ".tar"} or path.name.endswith(".img.gz")
        for path in collected
    )
    if not has_image:
        raise util.BuildError("no firmware image was produced")
    util.log(f"collected {len(collected)} file(s) in {out_dir}")
    return collected
