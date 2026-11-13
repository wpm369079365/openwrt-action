"""Validated build settings.

Values come from the GitHub Actions inputs (or their defaults) and are checked
before any file in the source tree is modified. Keeping the validation here
means the shell-free workflow cannot pass a malformed address or SSID into a
``sed`` expression.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from . import profiles, util

DEFAULT_IP = "10.0.0.1"
DEFAULT_WIFI_NAME = "ZeroWrt"
DEFAULT_WIFI_PASSWORD = "1234567890"
DEFAULT_ROOT_PASSWORD = "password"
DEFAULT_DIST_NAME = "ZeroWrt"

DEVICE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
DIST_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")
WIFI_NAME_RE = re.compile(r"[\w .-]+", re.UNICODE)
WIFI_PASSWORD_RE = re.compile(r"[A-Za-z0-9!@#$%^&*()_+.:=?\-]{8,63}")


@dataclass(frozen=True)
class BuildSettings:
    """User supplied build settings."""

    ip: str = DEFAULT_IP
    root_password: str = DEFAULT_ROOT_PASSWORD
    wifi_name: str = DEFAULT_WIFI_NAME
    wifi_password: str = DEFAULT_WIFI_PASSWORD
    device: str = profiles.DEFAULT_DEVICE
    dist_name: str = DEFAULT_DIST_NAME
    ota_url: str = ""

    @classmethod
    def from_environment(cls, env: dict[str, str] | None = None) -> BuildSettings:
        """Read settings from ``ZEROWRT_*`` environment variables."""
        values = os.environ if env is None else env
        return cls(
            ip=values.get("ZEROWRT_IP") or DEFAULT_IP,
            root_password=values.get("ZEROWRT_ROOT_PASSWORD") or DEFAULT_ROOT_PASSWORD,
            wifi_name=values.get("ZEROWRT_WIFI_NAME") or DEFAULT_WIFI_NAME,
            wifi_password=values.get("ZEROWRT_WIFI_PASSWORD") or DEFAULT_WIFI_PASSWORD,
            device=values.get("ZEROWRT_DEVICE") or profiles.DEFAULT_DEVICE,
            dist_name=values.get("ZEROWRT_DIST") or DEFAULT_DIST_NAME,
            ota_url=values.get("ZEROWRT_OTA_URL") or "",
        ).validated()

    def with_overrides(self, **values: str | None) -> BuildSettings:
        """Return a copy where every non-None value replaces the current one."""
        current = self.__dict__.copy()
        for key, value in values.items():
            if value is not None:
                current[key] = value
        return BuildSettings(**current).validated()

    def validated(self) -> BuildSettings:
        """Return self after checking all values, raising BuildError on errors."""
        try:
            address = ipaddress.ip_address(self.ip)
        except ValueError as exc:
            raise util.BuildError(f"invalid LAN IP address: {self.ip!r}") from exc
        if address.version != 4:
            raise util.BuildError(f"LAN IP address must be IPv4: {self.ip!r}")
        if address.is_multicast or address.is_unspecified or address.is_loopback:
            raise util.BuildError(f"LAN IP address is not usable: {self.ip!r}")

        if not self.root_password or "\n" in self.root_password or "\r" in self.root_password:
            raise util.BuildError("root password must be a single non-empty line")
        if len(self.root_password) < 8:
            raise util.BuildError("root password must be at least 8 characters")
        if len(self.root_password) > 128:
            raise util.BuildError("root password must be at most 128 characters")

        if not self.wifi_name or self.wifi_name != self.wifi_name.strip():
            raise util.BuildError("WiFi name must not be empty or padded with spaces")
        if not WIFI_NAME_RE.fullmatch(self.wifi_name):
            raise util.BuildError("WiFi name may only contain letters, digits, spaces, dot, dash and underscore")
        if len(self.wifi_name.encode("utf-8")) > 32:
            raise util.BuildError("WiFi name must be at most 32 bytes (UTF-8)")
        for band in ("2g", "5g", "6g"):
            if len(self.ssid(band).encode("utf-8")) > 32:
                raise util.BuildError(f"WiFi SSID is longer than 32 bytes: {self.ssid(band)!r}")

        if not self.wifi_password:
            raise util.BuildError("WiFi password is required (8-63 characters)")
        if not WIFI_PASSWORD_RE.fullmatch(self.wifi_password):
            raise util.BuildError("WiFi password must be 8-63 characters using letters, digits and !@#$%^&*()_+.:=?-")

        if not DEVICE_RE.fullmatch(self.device) or profiles.find(self.device) is None:
            raise util.BuildError(f"invalid device profile name: {self.device!r}")

        if not DIST_RE.fullmatch(self.dist_name) or self.dist_name != self.dist_name.strip():
            raise util.BuildError(f"invalid distribution name: {self.dist_name!r}")

        if self.ota_url:
            parsed = urlparse(self.ota_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise util.BuildError(f"invalid OTA index URL: {self.ota_url!r}")
        return self

    def ssid(self, band: str) -> str:
        """Return the SSID for a radio band such as 2g or 5g."""
        suffix = {"2g": "2.4G", "5g": "5G", "6g": "6G"}.get(band, band.upper())
        return f"{self.wifi_name}-{suffix}"
