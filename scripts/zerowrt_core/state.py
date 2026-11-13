"""Build metadata shared by the workflow steps.

``.zerowrt-info.json`` lives at the root of the source tree. It is written
after the clone, completed after ``config apply`` and read again by ``ota
embed``, ``build collect`` and ``ota index``. Keeping one file means the
firmware, the release notes and the OTA index can never disagree about the
device, the version or the build date.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from . import util

INFO_FILE = ".zerowrt-info.json"


def path(tree: Path) -> Path:
    return Path(tree) / INFO_FILE


def read(tree: Path) -> dict:
    data = util.read_json(path(tree), default={})
    if not isinstance(data, dict):
        raise util.BuildError(f"{path(tree)} does not contain a JSON object")
    return data


def write(tree: Path, data: dict) -> dict:
    util.write_json(path(tree), data)
    return data


def update(tree: Path, **values) -> dict:
    data = read(tree)
    data.update({key: value for key, value in values.items() if value is not None})
    return write(tree, data)


def build_date(data: dict) -> int:
    value = data.get("build_date")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def version_for(build_date: int) -> str:
    if build_date <= 0:
        build_date = int(time.time())
    return datetime.fromtimestamp(build_date, UTC).strftime("%Y%m%d")


def iso_for(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def fingerprint(data: dict) -> str:
    """Short, log-safe identity of a build for cache and release notes."""
    parts = [
        str(data.get("profile", "")),
        str(data.get("version", "")),
        str(data.get("source_commit", ""))[:12],
    ]
    return "/".join(part for part in parts if part)
