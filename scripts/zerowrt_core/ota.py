"""OTA metadata, update index and gh-pages publishing.

The firmware carries ``luci-app-ota``; for it to see a new release the build
has to do three things:

1. write ``/etc/ota/build.json`` (device, version, build time) into the image
   *before* compilation;
2. publish a schema-2 index whose entry has the release asset URL and SHA256;
3. serve that index from a stable HTTPS URL.

This module owns all three. It deliberately keeps the index format documented
by luci-app-ota instead of importing the plugin's generator, so a change in the
plugin's CLI cannot break the release pipeline silently.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import state, util

IMAGE_SUFFIXES = (".bin", ".itb", ".img", ".img.gz", ".trx", ".tar")
BAD_TOKENS = (
    "initramfs",
    "recovery",
    "preloader",
    "bl31",
    "factory",
    "vmlinux",
    "kernel",
    "rootfs",
    "uImage",
)
HISTORY_LIMIT = 120
HISTORY_CHANGELOG_LIMIT = 1200

OTA_APP_DIR = Path("package") / "new" / "luci-app-ota"


def app_present(tree: Path) -> bool:
    """Is ``luci-app-ota`` part of this build?

    Profiles on a source tree without the OTA app (``Source.ota`` False) never
    clone it, so every OTA step has to stand down instead of writing metadata
    for a package the image does not contain.
    """
    return (Path(tree) / OTA_APP_DIR).is_dir()


def read_checksums(dist: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("SHA256SUMS", "sha256sums", "SHA256SUM", "sha256sum.txt"):
        path = Path(dist) / name
        if not path.exists():
            continue
        for line in util.read_text(path).splitlines():
            match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line.strip())
            if match:
                result[Path(match.group(2).strip()).name] = match.group(1).lower()
    return result


def read_build_info(dist: Path) -> dict[str, str]:
    path = Path(dist) / "BUILD_INFO.txt"
    if not path.exists():
        return {}
    info: dict[str, str] = {}
    for line in util.read_text(path).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            info[key.strip().lower()] = value.strip()
    return info


def select_image(dist: Path) -> Path:
    """Pick the sysupgrade image to publish; never an initramfs or bootloader."""
    candidates = []
    for path in sorted(Path(dist).iterdir()):
        if not path.is_file() or not path.name.endswith(IMAGE_SUFFIXES):
            continue
        lowered = path.name.lower()
        if any(token.lower() in lowered for token in BAD_TOKENS):
            continue
        candidates.append(path)
    if not candidates:
        raise util.BuildError(
            f"no OTA image found in {dist}; expected a sysupgrade .bin/.itb/.img file"
        )

    def score(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        rank = 0
        if "sysupgrade" in name:
            rank += 20
        if "squashfs" in name:
            rank += 5
        if name.endswith(".itb"):
            rank += 3
        if "initramfs" in name:
            rank -= 50
        return (-rank, 0 if "ext4" in name else 1, name)

    return sorted(candidates, key=score)[0]


def device_keys(info: dict) -> list[str]:
    keys = []
    for value in (info.get("device"), info.get("dt_model"), info.get("profile"), info.get("artifact")):
        value = str(value or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _run_url() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repository and run_id:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""


def _changelog(info: dict, dist_name: str, build_date: int = 0) -> str:
    parts = [
        f"**{dist_name} {info.get('version', '')}**",
        f"Device: `{info.get('device', '')}`",
        f"Target: `{info.get('board', '')}/{info.get('subtarget', '')}`",
        f"Built: {state.iso_for(build_date or state.build_date(info))}",
    ]
    repository = info.get("source_repo", "")
    commit = str(info.get("source_commit", ""))[:12]
    if repository and commit:
        parts.append(f"Source: [{repository}@{commit}](https://github.com/{repository}/commit/{commit})")
    run_url = _run_url()
    if run_url:
        parts.append(f"[Workflow run]({run_url})")
    return "\n\n".join(parts)


def _asset_url(repo: str, tag: str, filename: str, url_base: str | None) -> str:
    if url_base:
        return f"{url_base.rstrip('/')}/{filename}"
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def _load_index(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {}
    try:
        data = util.read_json(Path(path), default={})
        return data if isinstance(data, dict) else {}
    except util.BuildError:
        return {}


def _load_history(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return {"schema": 1, "releases": []}
    try:
        data = util.read_json(Path(path), default={})
    except util.BuildError:
        return {"schema": 1, "releases": []}
    if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
        return {"schema": 1, "releases": []}
    return data


def _history_record(entry: dict) -> dict:
    record = {
        key: entry[key]
        for key in ("version", "build_date", "url", "sha256", "size", "file", "target", "device", "label")
        if key in entry
    }
    record["changelog"] = str(entry.get("changelog", ""))[:HISTORY_CHANGELOG_LIMIT]
    return record


def update_history(path: Path, entry: dict, *, dist_name: str, limit: int = HISTORY_LIMIT) -> None:
    history = _load_history(path)
    releases = [item for item in history.get("releases", []) if isinstance(item, dict)]
    key = (str(entry.get("device", "")), int(entry.get("build_date") or 0))
    if not any((str(item.get("device", "")), int(item.get("build_date") or 0)) == key for item in releases):
        releases.append(_history_record(entry))
    releases.sort(key=lambda item: (-int(item.get("build_date") or 0), str(item.get("device", ""))))
    data = {
        "schema": 1,
        "generated": int(time.time()),
        "dist": dist_name,
        "releases": releases[:limit],
    }
    util.write_json(Path(path), data)


def generate_index(
    dist: Path,
    *,
    info: dict,
    repo: str,
    tag: str,
    existing: Path | None = None,
    history: Path | None = None,
    changelog: str = "",
    url_base: str | None = None,
) -> dict:
    dist = Path(dist)
    image = select_image(dist)
    checksums = read_checksums(dist)
    build_info = read_build_info(dist)

    version = str(info.get("version") or build_info.get("version") or "")
    build_date = state.build_date(info)
    if build_date <= 0:
        built = build_info.get("built", "")
        for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S"):
            try:
                build_date = int(datetime.strptime(built, fmt).replace(tzinfo=UTC).timestamp())
                break
            except ValueError:
                continue
    if build_date <= 0:
        build_date = int(time.time())
    if not version:
        version = state.version_for(build_date)

    device = str(info.get("device") or build_info.get("device", "").split(" ")[0])
    if not device:
        raise util.BuildError("OTA index needs a device name; .zerowrt-info.json is missing it")
    target = str(info.get("target") or build_info.get("tree", ""))
    label = " ".join(
        part for part in (info.get("device_vendor", ""), info.get("device_model", "")) if part
    ).strip()
    digest = checksums.get(image.name) or util.sha256_file(image)
    size = image.stat().st_size
    url = _asset_url(repo, tag, image.name, url_base)

    entry = {
        "version": version,
        "build_date": build_date,
        "url": url,
        "sha256": digest,
        "size": size,
        "file": image.name,
        "target": target,
        "device": device,
        "changelog": changelog or _changelog(info, str(info.get("dist") or "ZeroWrt"), build_date),
    }
    if label:
        entry["label"] = label

    existing_data = _load_index(existing)
    devices = existing_data.get("devices") if isinstance(existing_data.get("devices"), dict) else {}
    devices = dict(devices)
    for key in device_keys(info):
        devices[key] = dict(entry)

    index = {
        "schema": 2,
        "generator": "zerowrt ota index",
        "generated": int(time.time()),
        "dist": str(info.get("dist") or "ZeroWrt"),
        "devices": dict(sorted(devices.items())),
    }
    util.write_json(dist / "ota.json", index)
    if history is not None:
        update_history(history, entry, dist_name=index["dist"])
    util.log(f"OTA index: {image.name} -> {', '.join(device_keys(info))}")
    util.log(f"OTA image sha256 {digest[:16]}..., {size} bytes")
    return index


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def embed(
    tree: Path,
    *,
    info: dict,
    api_url: str,
    download_proxy: str = "",
    emit: Path | None = None,
) -> dict:
    """Write the OTA metadata and first-boot defaults into the source tree."""
    tree = Path(tree)
    build_date = state.build_date(info)
    if build_date <= 0:
        build_date = int(time.time())
    version = str(info.get("version") or state.version_for(build_date))
    device = str(info.get("device") or "")
    if not device:
        raise util.BuildError("cannot embed OTA metadata without a device name")
    target = f"{info.get('board', '')}/{info.get('subtarget', '')}".strip("/")
    product = " ".join(
        part for part in (info.get("device_vendor", ""), info.get("device_model", "")) if part
    ).strip()

    metadata = {
        "dist": str(info.get("dist") or "ZeroWrt"),
        "version": version,
        "device": device,
        "model": str(info.get("dt_model") or ""),
        "target": target,
        "build_date": build_date,
    }
    if product:
        metadata["product"] = product

    destination = tree / "package" / "base-files" / "files" / "etc" / "ota" / "build.json"
    util.write_json(destination, metadata)
    util.log(f"OTA metadata: {destination.relative_to(tree)}")

    if api_url:
        defaults = tree / "package" / "base-files" / "files" / "etc" / "uci-defaults" / "90-luci-app-ota"
        lines = [
            "#!/bin/sh",
            "# Fill in the default index URL when the user has not configured one.",
            "[ -f /etc/config/ota ] || exit 0",
            (
                "[ -n \"$(uci -q get ota.config.api_url)\" ] || "
                f"uci -q set ota.config.api_url={_shell_quote(api_url)}"
            ),
        ]
        if download_proxy:
            lines.append(
                "[ -n \"$(uci -q get ota.config.download_proxy)\" ] || "
                f"uci -q set ota.config.download_proxy={_shell_quote(download_proxy)}"
            )
        lines.extend(["uci -q commit ota", "exit 0", ""])
        util.write_text(defaults, "\n".join(lines))
        defaults.chmod(0o755)
        util.log(f"OTA default index: {api_url}")
    else:
        util.warn("no OTA index URL supplied; the image will not know where to check for updates")

    util.write_text(tree / "version.date", f"{build_date}\n")
    util.log(f"version.date = {build_date}")

    if emit is not None:
        util.write_json(Path(emit), metadata)
        util.log(f"OTA metadata copy: {emit}")
    return metadata


def _brand_site(site: Path, repo: str) -> None:
    for path in Path(site).rglob("*"):
        if ".git" in path.parts:
            continue
        if not path.is_file() or path.suffix not in {".js", ".html", ".css", ".json", ".md"}:
            continue
        text = util.read_text(path)
        updated = text.replace("MinimaxFlora/ZeroWrt", repo)
        updated = updated.replace("https://github.com/MinimaxFlora/ZeroWrt", f"https://github.com/{repo}")
        if updated != text:
            util.write_text(path, updated)


def _copy_site(site: Path, target: Path, *, cname: str) -> None:
    site = Path(site)
    target = Path(target)
    if site.is_dir():
        # Never copy the upstream project's CNAME: it points at a different
        # domain. An existing gh-pages CNAME is preserved unless --cname is
        # given, so re-publishing does not silently drop a custom domain.
        shutil.copytree(
            site,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("CNAME", "ota.json", "history.json"),
        )
        util.log(f"site assets copied from {site}")
    if cname:
        util.write_text(target / "CNAME", cname + "\n")
        util.log(f"CNAME -> {cname}")
    (target / ".nojekyll").write_text("", encoding="utf-8")


def _initialise_checkout(checkout: Path, repo: str, branch: str) -> None:
    checkout.mkdir(parents=True, exist_ok=True)
    util.run(["git", "init", "-q", str(checkout)], quiet=True)
    util.run(["git", "-C", str(checkout), "checkout", "-q", "-b", branch], quiet=True)
    util.run(["git", "-C", str(checkout), "remote", "add", "origin", f"https://github.com/{repo}.git"], quiet=True)
    util.run(["git", "-C", str(checkout), "config", "user.name", "ZeroWrt Builder"], quiet=True)
    util.run(["git", "-C", str(checkout), "config", "user.email", "builder@zerowrt.invalid"], quiet=True)
    util.write_text(checkout / ".nojekyll", "")


def _checkout_gh_pages(checkout: Path, repo: str, branch: str, token: str | None) -> bool:
    """Clone *branch*; return False when the branch does not exist yet."""
    env = util.git_environment(token)
    code, output = util.run(
        ["git", "ls-remote", "--heads", f"https://github.com/{repo}.git", branch],
        env=env,
        capture=True,
        quiet=True,
        check=False,
    )
    if code != 0:
        raise util.BuildError(f"cannot query {repo} for the {branch} branch:\n{output[-2000:]}")
    if not output.strip():
        util.log(f"{branch} branch does not exist yet; creating it")
        _initialise_checkout(checkout, repo, branch)
        return False
    util.clone(repo, checkout, ref=branch, token=token, depth=1)
    util.run(["git", "-C", str(checkout), "checkout", "-q", "-B", branch], quiet=True)
    util.run(["git", "-C", str(checkout), "config", "user.name", "ZeroWrt Builder"], quiet=True)
    util.run(["git", "-C", str(checkout), "config", "user.email", "builder@zerowrt.invalid"], quiet=True)
    return True


def _commit_and_push(checkout: Path, repo: str, branch: str, token: str | None, message: str) -> None:
    util.run(["git", "-C", str(checkout), "add", "-A"], quiet=True)
    code, _ = util.run(["git", "-C", str(checkout), "diff", "--cached", "--quiet"], check=False, quiet=True)
    if code == 0:
        util.log("OTA page is already up to date; nothing to commit")
        return
    util.run(["git", "-C", str(checkout), "commit", "-q", "-m", message], quiet=True)
    env = util.git_environment(token)
    util.run(
        ["git", "-C", str(checkout), "push", "origin", f"HEAD:refs/heads/{branch}"],
        env=env,
        display=f"git push origin {branch}",
    )
    util.log(f"published to {repo}@{branch}")


def publish(
    *,
    dist: Path,
    info: dict,
    repo: str,
    tag: str,
    branch: str = "gh-pages",
    site: Path | None = None,
    cname: str = "",
    token: str | None = None,
    no_push: bool = False,
    out: Path | None = None,
) -> dict:
    dist = Path(dist)
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    if no_push:
        checkout = Path(out) if out else dist / "site"
        if checkout.exists():
            shutil.rmtree(checkout)
        checkout.mkdir(parents=True, exist_ok=True)
        existing = checkout / "ota.json"
        history = checkout / "history.json"
    else:
        checkout = Path(tempfile.mkdtemp(prefix="zerowrt-pages-"))
        _checkout_gh_pages(checkout, repo, branch, token)
        existing = checkout / "ota.json"
        history = checkout / "history.json"

    if site is not None:
        _copy_site(site, checkout, cname=cname)
        # Brand the copied page with the actual repository name. This runs
        # before the fresh ota.json is written, so only site assets change.
        _brand_site(checkout, repo)
    elif cname:
        util.write_text(checkout / "CNAME", cname + "\n")

    index = generate_index(
        dist,
        info=info,
        repo=repo,
        tag=tag,
        existing=existing,
        history=history,
    )
    util.write_json(checkout / "ota.json", index)
    util.write_json(dist / "ota.json", index)
    util.write_text(checkout / ".nojekyll", "")

    if not no_push:
        _commit_and_push(
            checkout,
            repo,
            branch,
            token,
            f"OTA index: {info.get('device', '')} {info.get('version', '')}",
        )
        shutil.rmtree(checkout, ignore_errors=True)
    else:
        util.log(f"OTA site written to {checkout} (no push)")
    return index
