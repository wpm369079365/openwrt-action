# configs/

每个 profile 一份设备配置，**这里是权威副本**——改包、加插件都改这里。

| 文件 | profile | 源码树 | 代理栈 | OTA |
|---|---|---|---|---|
| `ruijie-rg-x60-new-2410-homeproxy.config` | `ruijie_rg-x60-new-2410-homeproxy` | ImmortalWrt 24.10 | HomeProxy + sing-box | 无 |
| `ruijie-rg-x60-new-2410.config` | `ruijie_rg-x60-new-2410` | ImmortalWrt 24.10 | 无 | 无 |
| `ruijie-rg-x60-new.config` | `ruijie_rg-x60-new` | ImmortalWrt 25.12 | HomeProxy + sing-box | 有 |

构建时这些文件会覆盖 `pipeline/configs/` 里的同名文件（ZeroWrt 自带一份作为默认），
日志第一步会打印 `using local config ...` 说明用了哪份。

## 改配置的注意事项

- `REQUIRED_SELECTIONS`（ZeroWrt 侧）只校验渲染后 `.config` 里真正 `=y` 的项；写了某个包但源码树没有，
  `defconfig` 会静默丢掉，不会报错，但镜像里也就没有。
- `luci-app-homeproxy` 依赖 `sing-box`，而 `sing-box` 默认打开 `SING_BOX_BUILD_NAIVE_OUTBOUND`，
  会拖入本树没有的 `libcronet`（需要 gn/host）。所以配置里必须显式：
  `# CONFIG_SING_BOX_BUILD_NAIVE_OUTBOUND is not set`（tiny 变体同理）。
- 加第三方包之前，先在 ZeroWrt 的 `scripts/zerowrt_core/packages.py` 里确认它会被克隆进源码树，
  或者在 `diy-part1.sh` 里自己 clone。
- 只删包不改 `REQUIRED_SELECTIONS` 是安全的（校验只看配置实际要的），反过来把必需包删掉会失败——
  那正是校验存在的意义。
