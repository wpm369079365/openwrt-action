#!/usr/bin/env python3
"""ZeroWrt build CLI.

Every firmware customisation, patch, build step and OTA action is implemented
in Python so the GitHub Actions workflow only calls subcommands. The source
tree stays a stock OpenWrt/ImmortalWrt checkout; nothing here requires a shell
script in the repository.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zerowrt_core import (  # noqa: E402
    build,
    cache,
    config,
    customize,
    ota,
    packages,
    patches,
    profiles,
    settings,
    source,
    state,
    sysops,
    util,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TREE = Path(os.environ.get("ZEROWRT_SOURCE_DIR") or "/builder/source")
DEFAULT_ASSETS = REPO_ROOT / "assets"


def _jobs(value: str | None) -> int:
    if value:
        return max(1, int(value))
    return max(1, os.cpu_count() or 2)


def _settings(args: argparse.Namespace) -> settings.BuildSettings:
    current = settings.BuildSettings.from_environment()
    return current.with_overrides(
        ip=getattr(args, "ip", None),
        root_password=getattr(args, "root_password", None),
        wifi_name=getattr(args, "wifi_name", None),
        wifi_password=getattr(args, "wifi_password", None),
        device=getattr(args, "device", None),
        dist_name=getattr(args, "dist_name", None),
        ota_url=getattr(args, "ota_url", None),
    )


def _export_env(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_ENV")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def cmd_system_free_disk(args: argparse.Namespace) -> None:
    sysops.free_disk(
        str(Path(args.build_dir)),
        reserve_gb=args.reserve_gb,
        swap_size_gb=args.swap_gb,
        min_free_gb=args.min_free_gb,
    )


def cmd_system_install_deps(args: argparse.Namespace) -> None:
    sysops.install_deps(docker=args.docker)


def cmd_system_install_llvm(args: argparse.Namespace) -> None:
    sysops.install_llvm(version=args.version, strict=args.strict)


def cmd_source_clone(args: argparse.Namespace) -> None:
    device = args.device or settings.BuildSettings.from_environment().device
    info = source.clone_source(Path(args.dest), device=device, ref=args.ref)
    _export_env(
        {
            "ZEROWRT_SOURCE_REPO": info["source_repo"],
            "ZEROWRT_SOURCE_REF": info["source_ref"],
            "ZEROWRT_SOURCE_COMMIT": info["source_commit"],
            "ZEROWRT_BUILD_DATE": str(info["build_date"]),
        }
    )


def cmd_source_feeds_update(args: argparse.Namespace) -> None:
    source.update_feeds(Path(args.tree))


def cmd_source_feeds_install(args: argparse.Namespace) -> None:
    source.install_feeds(Path(args.tree))


def cmd_source_customize(args: argparse.Namespace) -> None:
    build_settings = _settings(args)
    util.log(
        f"LAN IP {build_settings.ip}, WiFi {build_settings.ssid('2g')} / "
        f"{build_settings.ssid('5g')}, device {build_settings.device}"
    )
    customize.apply(
        Path(args.tree),
        build_settings,
        assets=Path(args.assets),
        repo_root=REPO_ROOT,
    )


def cmd_packages_add(args: argparse.Namespace) -> None:
    device = args.device or settings.BuildSettings.from_environment().device
    profile = profiles.find(device)
    # An unknown profile keeps the historical behaviour; a known one decides
    # whether this image carries the OTA app at all.
    with_ota = True if profile is None else profiles.source(profile).ota
    packages.add_external_packages(Path(args.tree), with_ota=with_ota)


def cmd_packages_ota(args: argparse.Namespace) -> None:
    packages.add_ota_app(Path(args.tree))


def cmd_patches_apply(args: argparse.Namespace) -> None:
    device = args.device or settings.BuildSettings.from_environment().device
    patches.apply_all(
        Path(args.tree),
        scripts=Path(args.scripts),
        profile=profiles.require(device),
    )


def cmd_config_apply(args: argparse.Namespace) -> None:
    build_settings = _settings(args)
    extra: dict[str, str | None] = {}
    for item in args.set or []:
        name, _, value = item.partition("=")
        if not name:
            raise util.BuildError(f"invalid --set value: {item!r}")
        extra[name] = value or "y"
    config.apply(
        Path(args.tree),
        device=build_settings.device,
        config_path=Path(args.config) if args.config else None,
        extra=extra,
        dist_name=build_settings.dist_name,
    )


def cmd_ota_embed(args: argparse.Namespace) -> None:
    tree = Path(args.tree)
    if not ota.app_present(tree):
        util.log("luci-app-ota is not part of this build; skipping the OTA metadata")
        return
    info = state.read(tree)
    api_url = args.api_url or os.environ.get("ZEROWRT_OTA_URL", "")
    metadata = ota.embed(
        tree,
        info=info,
        api_url=api_url,
        download_proxy=args.download_proxy,
        emit=Path(args.emit) if args.emit else None,
    )
    util.log(f"OTA metadata for {metadata['device']} {metadata['version']}")


def cmd_ota_index(args: argparse.Namespace) -> None:
    tree = Path(args.tree)
    if not ota.app_present(tree):
        util.log("luci-app-ota is not part of this build; skipping the OTA index")
        return
    info = state.read(tree)
    if (not args.repo or not args.tag) and not args.url_base:
        raise util.BuildError("ota index needs --repo/--tag or --url-base")
    ota.generate_index(
        Path(args.dist),
        info=info,
        repo=args.repo,
        tag=args.tag,
        existing=Path(args.existing) if args.existing else None,
        history=Path(args.history) if args.history else None,
        changelog=args.changelog,
        url_base=args.url_base,
    )


def cmd_ota_publish(args: argparse.Namespace) -> None:
    tree = Path(args.tree)
    if not ota.app_present(tree):
        util.log("luci-app-ota is not part of this build; skipping the OTA download page")
        return
    info = state.read(tree)
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise util.BuildError("ota publish needs --repo or GITHUB_REPOSITORY")
    tag = args.tag or info.get("artifact") or info.get("profile") or ""
    if not tag:
        raise util.BuildError("ota publish needs a release tag")
    ota.publish(
        dist=Path(args.dist),
        info=info,
        repo=repo,
        tag=tag,
        branch=args.branch,
        site=Path(args.site) if args.site else None,
        cname=args.cname,
        token=args.token,
        no_push=args.no_push,
        out=Path(args.out) if args.out else None,
    )


def cmd_build_download(args: argparse.Namespace) -> None:
    build.download_sources(Path(args.tree), _jobs(args.jobs))


def cmd_build_compile(args: argparse.Namespace) -> None:
    build.compile_firmware(Path(args.tree), _jobs(args.jobs))


def cmd_build_collect(args: argparse.Namespace) -> None:
    build.collect(Path(args.tree), Path(args.out))


def cmd_cache_restore(args: argparse.Namespace) -> None:
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise util.BuildError("cache restore needs --repo or GITHUB_REPOSITORY")
    cache.restore(Path(args.tree), repo=repo, release=args.release, optional=not args.required)


def cmd_cache_save(args: argparse.Namespace) -> None:
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise util.BuildError("cache save needs --repo or GITHUB_REPOSITORY")
    cache.save(Path(args.tree), repo=repo, release=args.release)


def _add_tree(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tree", default=str(DEFAULT_TREE), help="path to the OpenWrt source tree")


def _add_build_settings(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", default=None, help="device profile (default: ZEROWRT_DEVICE)")
    parser.add_argument("--ip", default=None, help="LAN IP (default: ZEROWRT_IP or 10.0.0.1)")
    parser.add_argument(
        "--root-password",
        default=None,
        help="root password written to /etc/shadow (default: ZEROWRT_ROOT_PASSWORD or 'password')",
    )
    parser.add_argument("--wifi-name", default=None, help="WiFi name prefix (default: ZEROWRT_WIFI_NAME)")
    parser.add_argument("--wifi-password", default=None, help="WiFi password, 8-63 characters")
    parser.add_argument("--dist-name", default=None, help="distribution name (default: ZeroWrt)")
    parser.add_argument("--ota-url", default=None, help="default OTA index URL baked into the image")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zerowrt",
        description="ZeroWrt firmware build helper (MT798x, Python)",
    )
    parser.add_argument("--version", action="version", version="zerowrt 2.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    system = sub.add_parser("system", help="prepare the GitHub Actions runner")
    system_sub = system.add_subparsers(dest="system_command", required=True)

    free_disk = system_sub.add_parser("free-disk", help="remove unused software and merge disks")
    free_disk.add_argument("--build-dir", default="/builder")
    free_disk.add_argument("--swap-gb", type=int, default=4)
    free_disk.add_argument("--reserve-gb", type=int, default=6)
    free_disk.add_argument("--min-free-gb", type=float, default=50.0)
    free_disk.set_defaults(func=cmd_system_free_disk)

    deps = system_sub.add_parser("install-deps", help="install OpenWrt build dependencies")
    deps.add_argument("--docker", action="store_true", help="also install Docker")
    deps.set_defaults(func=cmd_system_install_deps)

    llvm = system_sub.add_parser("install-llvm", help="install the Clang toolchain")
    llvm.add_argument("--version", default=sysops.CLANG_VERSION)
    llvm.add_argument("--strict", action="store_true", help="fail when the download fails")
    llvm.set_defaults(func=cmd_system_install_llvm)

    src = sub.add_parser("source", help="upstream source tree operations")
    src_sub = src.add_subparsers(dest="source_command", required=True)

    clone = src_sub.add_parser("clone", help="clone the source tree for a device")
    clone.add_argument("--dest", default=str(DEFAULT_TREE))
    clone.add_argument("--device", default=None, help="device profile (default: ZEROWRT_DEVICE)")
    clone.add_argument("--ref", default=None, help="branch/tag override")
    clone.set_defaults(func=cmd_source_clone)

    feeds_update = src_sub.add_parser("feeds-update", help="fetch every OpenWrt feed")
    _add_tree(feeds_update)
    feeds_update.set_defaults(func=cmd_source_feeds_update)

    feeds_install = src_sub.add_parser("feeds-install", help="install feeds and drop duplicate packages")
    _add_tree(feeds_install)
    feeds_install.set_defaults(func=cmd_source_feeds_install)

    customize_cmd = src_sub.add_parser("customize", help="apply version, IP, password, shell, ttyd and WiFi")
    _add_tree(customize_cmd)
    _add_build_settings(customize_cmd)
    customize_cmd.add_argument("--assets", default=str(DEFAULT_ASSETS))
    customize_cmd.set_defaults(func=cmd_source_customize)

    patches_cmd = sub.add_parser("patches", help="apply the refreshed source/feed patches")
    patches_sub = patches_cmd.add_subparsers(dest="patches_command", required=True)
    patches_apply = patches_sub.add_parser("apply", help="apply all patches and verify them")
    _add_tree(patches_apply)
    patches_apply.add_argument("--device", default=None, help="device profile (default: ZEROWRT_DEVICE)")
    patches_apply.add_argument("--scripts", default=str(REPO_ROOT / "scripts"))
    patches_apply.set_defaults(func=cmd_patches_apply)

    pkg = sub.add_parser("packages", help="add external packages")
    pkg_sub = pkg.add_subparsers(dest="packages_command", required=True)
    pkg_add = pkg_sub.add_parser("add", help="clone HomeProxy, Argon, Go/Rust, Docker and OTA")
    _add_tree(pkg_add)
    pkg_add.add_argument("--device", default=None, help="device profile (default: ZEROWRT_DEVICE)")
    pkg_add.set_defaults(func=cmd_packages_add)

    pkg_ota = pkg_sub.add_parser("ota", help="clone only luci-app-ota")
    _add_tree(pkg_ota)
    pkg_ota.set_defaults(func=cmd_packages_ota)

    config_cmd = sub.add_parser("config", help="apply the device .config")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_apply = config_sub.add_parser("apply", help="copy configs/<device>.config, run defconfig and verify")
    _add_tree(config_apply)
    _add_build_settings(config_apply)
    config_apply.add_argument("--config", default=None, help="override the config file")
    config_apply.add_argument(
        "--set",
        action="append",
        metavar="SYMBOL=VALUE",
        help="extra Kconfig assignment (repeatable)",
    )
    config_apply.set_defaults(func=cmd_config_apply)

    ota_cmd = sub.add_parser("ota", help="embed OTA metadata, build the index and publish it")
    ota_sub = ota_cmd.add_subparsers(dest="ota_command", required=True)

    ota_embed = ota_sub.add_parser("embed", help="write /etc/ota/build.json into the image")
    _add_tree(ota_embed)
    ota_embed.add_argument("--api-url", default=None, help="default update index URL")
    ota_embed.add_argument("--download-proxy", default="", help="default GitHub download proxy")
    ota_embed.add_argument("--emit", default="", help="also write the metadata to this file")
    ota_embed.set_defaults(func=cmd_ota_embed)

    ota_index = ota_sub.add_parser("index", help="write ota.json for the collected artifacts")
    _add_tree(ota_index)
    ota_index.add_argument("--dist", default="dist")
    ota_index.add_argument("--repo", default="", help="GitHub repository owner/name")
    ota_index.add_argument("--tag", default="", help="release tag holding the asset")
    ota_index.add_argument("--existing", default="", help="existing ota.json to merge")
    ota_index.add_argument("--history", default="", help="history.json to update")
    ota_index.add_argument("--changelog", default="", help="override the generated changelog")
    ota_index.add_argument("--url-base", default="", help="asset URL prefix instead of GitHub Releases")
    ota_index.set_defaults(func=cmd_ota_index)

    ota_publish = ota_sub.add_parser("publish", help="publish ota.json and the download page to gh-pages")
    _add_tree(ota_publish)
    ota_publish.add_argument("--dist", default="dist")
    ota_publish.add_argument("--repo", default="", help="GitHub repository owner/name")
    ota_publish.add_argument("--tag", default="", help="release tag (default: the profile name)")
    ota_publish.add_argument("--branch", default="gh-pages")
    ota_publish.add_argument("--site", default="", help="static download page directory")
    ota_publish.add_argument("--cname", default="", help="custom domain written to CNAME")
    ota_publish.add_argument("--token", default=None, help="GitHub token (default: GH_TOKEN)")
    ota_publish.add_argument("--no-push", action="store_true", help="write the site locally only")
    ota_publish.add_argument("--out", default="", help="output directory for --no-push")
    ota_publish.set_defaults(func=cmd_ota_publish)

    build_cmd = sub.add_parser("build", help="download, compile and collect")
    build_sub = build_cmd.add_subparsers(dest="build_command", required=True)
    download = build_sub.add_parser("download")
    _add_tree(download)
    download.add_argument("--jobs", default=None)
    download.set_defaults(func=cmd_build_download)

    compile_ = build_sub.add_parser("compile")
    _add_tree(compile_)
    compile_.add_argument("--jobs", default=None)
    compile_.set_defaults(func=cmd_build_compile)

    collect = build_sub.add_parser("collect")
    _add_tree(collect)
    collect.add_argument("--out", default="dist")
    collect.set_defaults(func=cmd_build_collect)

    cache_cmd = sub.add_parser("cache", help="toolchain cache in a GitHub release")
    cache_sub = cache_cmd.add_subparsers(dest="cache_command", required=True)
    cache_restore = cache_sub.add_parser("restore", help="restore build_dir/dl/staging_dir/tmp")
    _add_tree(cache_restore)
    cache_restore.add_argument("--repo", default="")
    cache_restore.add_argument("--release", default=cache.DEFAULT_RELEASE)
    cache_restore.add_argument("--required", action="store_true", help="fail when no cache exists")
    cache_restore.set_defaults(func=cmd_cache_restore)

    cache_save = cache_sub.add_parser("save", help="pack and upload the toolchain cache")
    _add_tree(cache_save)
    cache_save.add_argument("--repo", default="")
    cache_save.add_argument("--release", default=cache.DEFAULT_RELEASE)
    cache_save.set_defaults(func=cmd_cache_save)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except util.BuildError as exc:
        util.header("Build failed")
        for line in str(exc).splitlines():
            print(line, flush=True)
        first = str(exc).splitlines()[0] if str(exc) else "build failed"
        print(f"::error::{first}", flush=True)
        return 1
    except KeyboardInterrupt:
        print("::error::interrupted", flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
