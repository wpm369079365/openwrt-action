# 构建与维护

本文档面向维护者：每个构建步骤做什么、哪些文件承载定制、OTA 索引如何生成，以及
补丁失效后如何刷新。

`build.yml` / `toolchain.yml` 在本仓库的 `.github/workflows/` 下，调用 `scripts/zerowrt.py`。

## 1. 源码与设备

| profile | 源码仓库 | 分支 | 上游 Device 名 | 配置 |
| --- | --- | --- | --- | --- |
| `ruijie_rg-x60-new` | `chasey-dev/immortalwrt-mt798x-rebase` | `25.12` | `ruijie_rg-x60-new-mtkuboot` | `configs/ruijie-rg-x60-new.config` |
| `ruijie_rg-x60-new-2410` | `padavanonly/immortalwrt-mt798x-6.6` | `openwrt-24.10-6.6` | `ruijie_rg-x60-new` | `configs/ruijie-rg-x60-new-2410.config` |
| `ruijie_rg-x60-new-2410-homeproxy` | 同上 | 同上 | 同上 | `configs/ruijie-rg-x60-new-2410-homeproxy.config` |

三个 profile 的目标都是 `mediatek/filogic`，布局为 MTK U-Boot（24.10 树的上游设备名没有
`-mtkuboot` 后缀，但分区表与 sysupgrade-tar 格式一致，镜像可互刷）。

源码都是公开仓库，不需要 `SOURCE_TOKEN`。需要写入权限的只有 `GITHUB_TOKEN`
（Actions 自动提供）。

新增设备时：在 `configs/` 放一份完整 `.config`，在
`scripts/zerowrt_core/profiles.py` 的 `PROFILES` 里登记 `device`/`dt_model`，
然后把这个 profile 加到 `build.yml` / `toolchain.yml` 的 `device` 选项。OTA key 会自动取
profile 里的 `device`，不需要再维护一份列表。

## 2. 工作流步骤

`build.yml` 按下面的顺序调用 CLI。顺序不是随意的：补丁必须在 feed 安装前应用，
`feeds install` 必须在替换 feed 目录前执行；`package/new` 在 `feeds install` 之后克隆，
它的同名 feed 包会被清理。`.config` 必须在所有包就位后再 `defconfig`。

1. **free-disk**：删除 runner 上无用的 SDK，必要时用 LVM 合并 `/` 与 `/mnt`，
   创建 swap。根分区已经足够大时跳过清理。
2. **install-deps**：安装 OpenWrt 编译依赖、`ccache`、`lvm2`、`xfsprogs`、
   Python/Pillow 等。
3. **install-llvm**：下载 sbwml 的 Clang 23 到 `/opt/clang`，写入 `GITHUB_PATH`。
4. **source clone**：按 profile 克隆源码，写入 `.zerowrt-info.json`
   （仓库、分支、commit、构建时间、设备）。
5. **feeds update**：`./scripts/feeds update -a`，确认 `feeds/luci`、`feeds/packages`。
6. **feeds install**：`./scripts/feeds install -a`，确认 `luci-compat`、`lyaml`、
   `ttyd` 已安装。必须在克隆外部包之前执行，之后的替换会复用这些符号链接。
7. **packages add**：
   - `MinimaxFlora/openwrt_package` → `package/new/helloworld`（移除 `autocore`）；
   - `sbwml/packages_lang_golang` `27.x`；
   - `sbwml/packages_lang_rust`；
   - `luci-app-dockerman` + Docker 四个包（默认配置未选中，保留）；
   - `MinimaxFlora/luci-app-ota` → `package/new/luci-app-ota`；
   - `sbwml/luci-app-openlist2` → `package/new/openlist`（`openlist2` 服务端 +
     `luci-app-openlist2` 前端，取代已存档的 AList）；
   - 扫描 `package/new` 的 `PKG_NAME`/`BuildPackage`，删除 `feeds/` 与
     `package/feeds/` 里同名包。
8. **source customize**：版本、发行字符串、IP、主机名、banner、root 密码、
   bash、profile、ttyd、MTK WiFi 默认值。
9. **patches apply**：
   - `0001`–`0005` 应用到 `feeds/luci`；
   - `0006` 应用到源码根（GCC 16）；
   - 复制 `900-*` 到 elfutils / libwebsockets 的 `patches/`；
   - 给 xl2tpd 加 `-std=gnu17`，给 bash 加 `no-lto`；
   - 全部做反向检查，重复运行是 no-op。
10. **config apply**：复制设备配置，追加 `luci-app-ota` 与中文语言包，
    `make defconfig`，校验 target、OTA、Argon、HomeProxy（含 sing-box 与中文包）、
    OpenList、vlmcsd、bash、ttyd。校验只针对渲染后的 `.config` 实际置 `y` 的项，
    因此关闭某个前端的 profile 不会被它误判。
11. **ota embed**：写 `/etc/ota/build.json`、`version.date`、首次启动
    `/etc/uci-defaults/90-luci-app-ota`。
12. **cache restore**（可选）：从 `toolchain` Release 恢复构建缓存。
13. **build download**：`make -jN download`，失败后串行重试。
14. **build compile**：`make -j(nproc+1) IGNORE_ERRORS="n m"`，失败后
    `make -j1 ... V=s`。
15. **build collect**：收集镜像、`Packages.tar.gz`、`build.config`、
    `BUILD_INFO.txt`、`SHA256SUMS`。
16. **ota index**：生成 `dist/ota.json`。
17. Release：tag `ruijie_rg-x60-new`，上传 `dist/*`。
18. **ota publish**：更新 `gh-pages` 的 `ota.json`、`history.json` 和下载页。

## 3. 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ZEROWRT_SOURCE_DIR` | `/builder/source` | 源码树 |
| `ZEROWRT_DEVICE` | `ruijie_rg-x60-new` | profile |
| `ZEROWRT_IP` | `10.0.0.1` | LAN IP |
| `ZEROWRT_ROOT_PASSWORD` | `password` | root 密码（不打印） |
| `ZEROWRT_WIFI_NAME` | `ZeroWrt` | SSID 前缀 |
| `ZEROWRT_WIFI_PASSWORD` | `1234567890` | WiFi 密码（不打印） |
| `ZEROWRT_DIST` | `ZeroWrt` | 发行版名 |
| `ZEROWRT_OTA_URL` | 空 | 固件默认索引地址 |
| `ZEROWRT_CLANG_VERSION` | `23.0.1` | LLVM 版本 |

CLI 的 `--ip`、`--root-password`、`--wifi-name`、`--wifi-password`、`--dist-name`
会覆盖环境变量。

## 4. 定制内容

| 项目 | 实现 | 结果 |
| --- | --- | --- |
| 发行版 | `include/version.mk` | `VERSION_DIST`/`VERSION_MANUFACTURER` → `ZeroWrt` |
| 版本号 | `VERSION_NUMBER` | `YYYYMMDD`（构建日期） |
| 发布字符串 | `openwrt_release`、`os-release` | `ZeroWrt-YYYYMMDD`、`ZeroWrt 标准版 @R...` |
| LAN IP | `config_generate` | 所有 `ipad=${ipaddr:-...}` 默认值 |
| 主机名 | `config_generate` | `ZeroWrt` |
| root 密码 | `package/base-files/files/etc/shadow` | `openssl passwd -5` 哈希 |
| banner | `package/base-files/files/etc/banner` | `assets/banner` |
| shell | `passwd`、`profile`、`root/.bash*` | bash + 彩色提示符 + `/opt` PATH |
| ttyd | feed 里的 config/init/menu | 免登录、菜单顺序 50、关闭 stdout 日志 |
| WiFi | `mtwifi.uc` | `SSID-2.4G/5G/6G`，WPA2 + 密码 |
| 版本 | `version.date` | 构建时间戳，供 OTA 比较 |

root 密码通过 `openssl passwd -5 -stdin` 生成，密码不会出现在命令行或日志里。

## 5. OTA

### 5.1 固件内元数据

`ota embed` 写：

```json
{
  "dist": "ZeroWrt",
  "version": "20261102",
  "device": "ruijie_rg-x60-new-mtkuboot",
  "model": "ruijie,rg-x60-new-mtkuboot",
  "target": "mediatek/filogic",
  "build_date": 1793600000,
  "product": "Ruijie RG-X60 New"
}
```

同时写 `version.date`，并生成 `/etc/uci-defaults/90-luci-app-ota`，只在用户没有配置
`ota.config.api_url` 时写入默认索引地址。

### 5.2 索引格式

`ota index` 生成 schema 2：

```json
{
  "schema": 2,
  "generator": "zerowrt ota index",
  "generated": 1793600100,
  "dist": "ZeroWrt",
  "devices": {
    "ruijie_rg-x60-new-mtkuboot": {
      "version": "20261102",
      "build_date": 1793600000,
      "url": "https://github.com/<owner>/<repo>/releases/download/ruijie_rg-x60-new/<image>",
      "sha256": "...",
      "size": 33554432,
      "file": "<image>",
      "target": "mediatek/filogic",
      "device": "ruijie_rg-x60-new-mtkuboot",
      "label": "Ruijie RG-X60 New",
      "changelog": "..."
    },
    "ruijie,rg-x60-new-mtkuboot": { "...": "同一份 entry" },
    "ruijie_rg-x60-new": { "...": "同一份 entry" }
  }
}
```

三个 key 都写，是为了兼容只认设备树名或只认 profile 名的客户端；固件内
`build.json` 的 `device` 是第一个 key。

镜像选择规则：只接受 `.bin/.itb/.img/.img.gz/.trx/.tar`，排除
`initramfs`、`recovery`、`preloader`、`bl31`、`factory`、`kernel`、`rootfs` 等，
优先 `sysupgrade` + `squashfs`。`mtkuboot` 名字里含 `uboot` 不会被误删。

### 5.3 发布

`ota publish`：

1. clone `gh-pages`（不存在则创建 orphan 分支）；
2. 复制 `luci-app-ota/site` 静态页，删除上游的 `CNAME`（除非显式传 `--cname`）；
3. 合并已有 `ota.json` 的其它设备条目；
4. 追加 `history.json`（同一 device + build_date 去重，保留 120 条）；
5. 用 `GITHUB_TOKEN` 推回 `gh-pages`。

`--no-push --out dist/site` 可以在本地生成同样的页面，不联网。

### 5.4 Pages

Settings → Pages → Deploy from a branch → `gh-pages` / `/`。
默认索引地址是 `https://<owner>.github.io/<repo>/ota.json`。自定义域名见 README。

## 6. 补丁刷新

### 6.1 LuCI 补丁（0001–0005）

当前基线：`immortalwrt/luci` `openwrt-25.12`。刷新步骤：

```bash
git clone -b openwrt-25.12 https://github.com/immortalwrt/luci.git /tmp/luci
cd /tmp/luci
git config user.name 'ZeroWrt Builder'
git config user.email 'builder@zerowrt.invalid'
git checkout -b refresh
git am /path/to/repo/scripts/0001-*.patch
git am /path/to/repo/scripts/0002-*.patch
git am /path/to/repo/scripts/0003-*.patch
git am /path/to/repo/scripts/0004-*.patch
git am /path/to/repo/scripts/0005-*.patch
git format-patch -5 -o /tmp/refreshed --start-number 1
```

用生成的文件替换 `scripts/0001`–`0005`，然后验证：

```bash
git -C /tmp/luci checkout -- .
git -C /tmp/luci clean -fd
for p in scripts/000[1-5]-*.patch; do
  git -C /tmp/luci apply --check "$PWD/$p" || echo "FAILED $p"
done
```

如果补丁还在，但上游文件已经大幅改动，优先重新生成补丁，而不是加 `--3way`；
`patches.py` 只使用严格的 `git apply --check`，不会静默接受模糊匹配。

### 6.2 GCC 16（0006）

`0006` 增加 `toolchain/gcc/patches-16.x/*` 和 `Config.version` 的 16.1.0 选项。
它当前基于 `25.12` 的 `toolchain/gcc`，应用时偏移为 0。GCC 版本变化后需要重新
生成；否则 `configs/` 里的 `CONFIG_GCC_USE_VERSION_16=y` 会导致源码下载/配置失败。

### 6.3 包补丁（900-*）

- libwebsockets 4.4.1：`900-fix-build-for-gcc-16.patch` 删除 deaddrop 插件里
  未使用的变量，已用 `patch --dry-run` 对照 4.4.1 源码验证。
- elfutils 0.192：`900-fix-gcc16-null-dereference-with-lto.patch` 在
  `libelf/elf32_updatenull.c` 增加空指针检查，已验证可应用。

包版本升级后（`PKG_VERSION` 变化）需要重新检查。`patches.py` 会把补丁复制到包的
`patches/` 目录，构建时由 buildroot 应用；应用失败会在编译阶段报错，不会静默跳过。

## 7. 工具链缓存

`toolchain.yml` 编译 `make toolchain/compile`，然后：

```bash
python3 scripts/zerowrt.py cache save --tree /builder/source --repo <owner>/<repo>
```

资产名从 `staging_dir/toolchain-*` 推导：

```text
toolchain_<libc>_<arch>_gcc-<major>.tar.zst
```

`build.yml` 的 `cache restore` 只取与 `.config` 中 GCC 大版本匹配的最新资产，
解压后 touch `staging_dir`/`tmp`。缓存是**可选**的：没有资产时只警告，工具链照常编译。

## 8. 代码质量

```bash
python -m unittest discover -v
python -m ruff check scripts tests
```

测试不联网、不调用 `make`/`apt`，覆盖：settings 校验、config 渲染、设备解析、
定制幂等性、feed 冲突清理、补丁重复应用、产物收集、OTA 索引/历史/发布、
工具链资产名。

## 9. 从旧仓库迁移

- MediaTek Builder 的 `diy-script.sh` 已逐项翻译进
  `scripts/zerowrt_core/customize.py` 和 `packages.py`；
- 原来的 `curl | patch` 改成随仓库分发的、已刷新的补丁文件；
- 原来的 `make -j$(nproc)` 改成内存感知的 `nproc + 1`，并保留串行 `V=s` 兜底；
- 原来的 `cachewrtbuild` action 改成可选的 `toolchain` Release 缓存；
- 旧的 `build.sh`/`diy-script.sh` 不再存在；workflow 只调用 Python CLI。
