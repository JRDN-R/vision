#!/bin/sh
set -eu
# This container owns the network namespace shared by Cobalt. The extractor
# itself has NO NET_ADMIN capability and cannot remove these rules. An internal
# Docker network alone is insufficient: its bridge gateway can reach the host.
proxy_ip="$(getent hosts egress | awk 'NR == 1 { print $1 }')"
case "$proxy_ip" in
  ''|*[!0-9.]*) exit 1 ;;
esac
iptables -P OUTPUT DROP
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -d "$proxy_ip/32" -p tcp --dport 8080 -j ACCEPT
# Docker's embedded DNS is on loopback. No arbitrary DNS, direct Internet,
# link-local metadata, host bridge or sibling-container connections are allowed.
ip6tables -P OUTPUT DROP
ip6tables -F OUTPUT
ip6tables -A OUTPUT -o lo -j ACCEPT
ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
touch /run/policy-ready
exec tail -f /dev/null
