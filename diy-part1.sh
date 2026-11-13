#!/usr/bin/env bash
# Runs right after the source tree is cloned, before `feeds update`.
#
# Working directory: the OpenWrt source tree ($ZEROWRT_SOURCE_DIR).
# Typical use: rewrite feeds.conf.default, add a third-party feed.
#
# Anything that adds a package still has to be reflected in the device config,
# otherwise `defconfig` drops it and the build fails on the selection check.
set -euo pipefail

# Example: swap a feed for a fork.
# sed -i 's#^src-git luci .*#src-git luci https://github.com/wpm369079365/luci.git;openwrt-24.10#' feeds.conf.default

exit 0
