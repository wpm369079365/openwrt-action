# openwrt-action

锐界 RG-X60 New 的固件构建仓库，用法和 [P3TERX/Actions-OpenWrt](https://github.com/P3TERX/Actions-OpenWrt) 一样：
改仓库里的东西 → 点 Actions → Release 里拿固件。

## 这个仓库和 ZeroWrt 的关系

| | 位置 | 内容 |
|---|---|---|
| **构建引擎** | `wpm369079365/ZeroWrt` | `scripts/zerowrt.py`：拉源码、剪 feed、渲染 `.config`、克隆 helloworld/openlist 包、改 WiFi/IP、打补丁、OTA |
| **构建入口** | 本仓库 | 工作流、设备配置、DIY 钩子、产物 Release |

工作流运行时把 ZeroWrt 检出到 `pipeline/`，因此引擎修一次，这里立刻受益，不需要同步脚本。
固件、日志、OTA 页面都发在**本仓库**的 Release / gh-pages，跟 ZeroWrt 的产物完全分开。

## 怎么用

1. 打开 **Actions → Build firmware → Run workflow**
2. 选设备 profile，填 LAN IP / root 密码 / WiFi 名称密码
3. 跑完去 **Releases** 下载 `*-sysupgrade.tar`（或 `.bin`）

可选 profile：

| profile | 源码树 | 编译器 | 代理栈 | OTA |
|---|---|---|---|---|
| `ruijie_rg-x60-new-2410-homeproxy`（默认） | ImmortalWrt 24.10 | GCC 14 | HomeProxy + sing-box | 无 |
| `ruijie_rg-x60-new-2410` | ImmortalWrt 24.10 | GCC 14 | 无 | 无 |
| `ruijie_rg-x60-new` | ImmortalWrt 25.12 | GCC 16 | HomeProxy + sing-box | 有 |

三者分区布局一致（UBI 起于 `0x680000`），镜像可互刷。

## 目录

```
.github/workflows/build.yml     固件构建（薄壳，逻辑全在 ZeroWrt）
.github/workflows/toolchain.yml 工具链缓存（跑一次，后续省 ~30 分钟）
configs/                        设备配置，每个 profile 一份（权威副本）
diy-part1.sh                    feeds update 之前的钩子
diy-part2.sh                    feeds install + 外部包之后的钩子
assets/                         品牌图（banner / logo），覆盖 ZeroWrt 的（可选）
```

### configs/

三份设备配置就在这里，改包改这里。构建时会覆盖 `pipeline/configs/` 的同名文件，
日志第一步会打印 `using local config ...`。细节见 `configs/README.md`。

### diy-part1.sh / diy-part2.sh

两个钩子的工作目录都是**源码树**（P3TERX 习惯），可用环境变量：

- `ZEROWRT_SOURCE_DIR` — 源码树路径
- `PIPELINE` — ZeroWrt 检出目录
- `DIY_STAGE` — `part1` / `part2`

`diy-part1.sh` 在 `feeds update` 前执行，适合改 `feeds.conf.default`、加第三方 feed。
`diy-part2.sh` 在 feed 装完、外部包克隆完之后执行，适合改源码、往 `files/` 塞配置、
替换主题资源。两者默认是空脚本（直接 `exit 0`），不会影响构建。

## 加速

第一次构建要连工具链一起编（约多 30–40 分钟）。先跑一次
**Actions → Build toolchain cache**，产物会作为 `toolchain_<libc>_<arch>_gcc-<major>.tar.zst`
挂到本仓库的 `toolchain` release 上，之后 `Build firmware` 打开 `cache_toolchain` 就会还原。

23 分钟内编不完可以把 `timeout-minutes` 调大；24.10 树不需要 LLVM，工作流已按 profile 自动跳过。

## 注意

- 24.10 两个 profile 的镜像**不带 OTA**，只能手动刷；OTA 相关步骤会自动跳过（日志里会说明）。
- `ruijie_rg-x60-new`（25.12）这套配置切到 HomeProxy 后**尚未经过实际构建验证**，第一次跑可能要处理上游差异。
- 固件 Release 的 tag 就是 profile 名，重跑会覆盖同名资产（`overwrite_files: true`）。
