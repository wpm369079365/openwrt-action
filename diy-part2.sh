#!/usr/bin/env bash
# Runs after the feeds and the external package set are in place, before the
# in-tree customisations and the device .config.
#
# Working directory: the OpenWrt source tree ($ZEROWRT_SOURCE_DIR).
# Typical use: patch a package, drop files into files/, tweak a default.
#
# Kernel and package defaults that belong in the image should go to feeds/ or
# package/*/files/ here; anything that changes the package selection belongs in
# configs/*.config instead.
set -euo pipefail

# Example: ship a custom banner.
# mkdir -p files/etc
# echo "Welcome to my router" > files/etc/banner

exit 0
