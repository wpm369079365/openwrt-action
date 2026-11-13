#!/bin/sh
# ddns IP source script (IPv6) for luci-app-ddns "IP 地址来源 = 脚本"
#
# Prints the global IPv6 address with the longest preferred lifetime on the LAN
# bridge. When the ISP delegates a new prefix the old address lingers for a
# while as deprecated/decaying; picking the longest preferred lifetime tracks
# the prefix that is actually routing right now.
#
# Override the interface with: DDNS6_IFACE=wan6 /usr/lib/ddns/get_ipv6.sh
IF="${DDNS6_IFACE:-br-lan}"

addr=$(ip -6 -o addr show dev "$IF" scope global 2>/dev/null | awk '
{
	ip=""; pl=0
	for (i=1;i<=NF;i++) {
		if ($i=="inet6") { ip=$(i+1); sub(/\/.*/,"",ip) }
		if ($i=="preferred_lft") {
			v=$(i+1); sub(/sec$/,"",v)
			pl = (v=="forever") ? 999999 : v+0
		}
	}
	if (ip ~ /^[23]/ && $0 !~ /deprecated/) print pl, ip
}' | sort -rn | head -n1 | cut -d' ' -f2)

# fall back to the WAN side if the bridge has no global address yet
if [ -z "$addr" ]; then
	for ifc in wan6 wan pppoe-wan; do
		addr=$(ip -6 -o addr show dev "$ifc" scope global 2>/dev/null \
			| awk '$0 !~ /deprecated/ { for (i=1;i<=NF;i++) if ($i=="inet6") { a=$(i+1); sub(/\/.*/,"",a); if (a ~ /^[23]/) { print a; exit } } }')
		[ -n "$addr" ] && break
	done
fi

[ -n "$addr" ] && echo "$addr"
