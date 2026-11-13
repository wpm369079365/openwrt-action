# openwrt-action

锐捷 RG-X60 New 的固件构建仓库，用法和 [P3TERX/Actions-OpenWrt](https://github.com/P3TERX/Actions-OpenWrt)、
[lgs2007m/Actions-OpenWrt](https://github.com/lgs2007m/Actions-OpenWrt) 一样：
改仓库里的东西 → 点 Actions → Release 里拿固件。

**自包含**：源码树在构建时从上游 clone，其余全部在这个仓库里——流水线、设备配置、DIY 钩子、
品牌资源。以前放在 `ZeroWrt` 仓库的那套东西已经全部搬过来了，不再有跨仓库依赖。

## 怎么用

1. 打开 **Actions → Build firmware → Run workflow**
2. 选设备 profile，填 LAN IP / root 密码 / WiFi 名称密码
3. 跑完去 **Releases** 下载 `*-sysupgrade.tar`（或 `.bin`）

可选 profile：

| profile | 源码树 | 编译器 | mt_wifi | 代理栈 | OTA |
|---|---|---|---|---|---|
| `ruijie_rg-x60-new-2410-homeproxy`（默认） | ImmortalWrt 24.10 | GCC 14 | 7.6.6.1 | HomeProxy + sing-box | 有 |
| `ruijie_rg-x60-new-2410` | ImmortalWrt 24.10 | GCC 14 | 7.6.6.1 | 无 | 有 |

两个 profile 只有包选不同（HomeProxy 那一份多 `sing-box`），分区布局都是 UBI 起于 `0x680000`。

## 源码

唯一来源是 **[padavanonly/immortalwrt-mt798x-6.6](https://github.com/padavanonly/immortalwrt-mt798x-6.6)**
的 `openwrt-24.10-6.6` 分支。别的树（例如把 MTK feeds 补丁导入 ImmortalWrt 25.12 的第三方
rebase）不接进来：驱动、内核和补丁都是在 padavanonly 这棵树上验证的，换树等于换固件。

## 关于“驱动版本”

lgs2007m 那些 MT7981 工作流里能直接下拉选 `7.6.7.2-fw-20240823` 之类，是因为 hanwckf 把
`mt_wifi` 拆成了「驱动 + 固件 blob」的多个预编译包。padavanonly 这棵树不是这个结构：
驱动是源码包 `package/mtk/drivers/mt_wifi`，版本跟着分支走，固件 blob 也随分支一起更新。
当前 `openwrt-24.10-6.6` 上是 **7.6.6.1**。

想试 padavanonly 的其它分支，用 `source_ref` 覆盖：填 `mt798x-mt799x-6.6-mtwifi` 之类即可，
留空就用 profile 自带的分支。

## 目录

```
.github/workflows/build.yml     固件构建（20 步）
.github/workflows/toolchain.yml 工具链缓存（跑一次，后续省 ~30 分钟）
scripts/                        流水线：zerowrt.py + zerowrt_core/（sysops/source/
                                packages/customize/config/patches/build/ota/cache/state）
scripts/000*.patch, 900-*.patch 已刷新/已验证的补丁
configs/                        设备配置，每个 profile 一份
tests/                          不联网的单元测试
assets/                         banner / logo
diy-part1.sh                    feeds update 之前的钩子
diy-part2.sh                    feeds install + 外部包之后的钩子
docs/BUILD.md                   每个构建步骤做什么、补丁怎么刷新
```

### configs/

两份设备配置，改包改这里。`scripts/zerowrt_core/profiles.py` 里的 profile 决定用哪份；
配置里 `=y` 的项必须在源码树里真实存在，否则 `defconfig` 会静默丢掉——细节见
`configs/README.md`。

### diy-part1.sh / diy-part2.sh

两个钩子的工作目录都是**源码树**（P3TERX 习惯），可用环境变量：

- `ZEROWRT_SOURCE_DIR` — 源码树路径
- `PIPELINE` — 本仓库的检出目录
- `DIY_STAGE` — `part1` / `part2`

`diy-part1.sh` 在 `feeds update` 前执行，适合改 `feeds.conf.default`、加第三方 feed。
`diy-part2.sh` 在 feed 装完、外部包克隆完之后执行，适合改源码、往 `files/` 塞配置、
替换主题资源。两者默认是空脚本（直接 `exit 0`），不会影响构建。

## 加速

第一次构建要连工具链一起编。先跑一次 **Actions → Build toolchain cache**，产物会作为
`toolchain_<libc>_<arch>_gcc-<major>.tar.zst` 挂到本仓库的 `toolchain` release 上，
之后 `Build firmware` 打开 `cache_toolchain` 就会还原，省 30–40 分钟。

24.10 树用 GCC 14，不需要 LLVM，工作流里已经没有那一步。

## 注意

- 所有 profile 都带 `luci-app-ota`（系统 → OTA 固件更新）。第一次用之前去 **Settings → Pages**
  选 `gh-pages` 分支发布一次，否则固件默认查的 `https://<owner>.github.io/<repo>/ota.json` 是 404。
- 固件 Release 的 tag 就是 profile 名，重跑会覆盖同名资产（`overwrite_files: true`）。
- 版权与许可见 `LICENSE`；流水线与补丁来自 ZeroWrt 项目，原作者信息保留在文件头。
