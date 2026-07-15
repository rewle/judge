#!/bin/sh
# Сначала — единственный разрешённый DNS-резолв внутри сендбокса, потом —
# полная блокировка сети, кроме адреса стенда. Порядок важен: резолв ДО
# блокировки. См. docs/09_scenario_emulator.md, "Sandbox стенда для Фазы 1"
# и scenario_emulator/docker_sandbox.py (_resolve_stand_host).
set -e

if [ -z "$STAND_HOST" ] || [ -z "$STAND_PORT" ] || [ -z "$STAND_SCHEME" ]; then
  echo "STAND_HOST/STAND_PORT/STAND_SCHEME не заданы" >&2
  exit 1
fi

STAND_IP=$(python3 -c "import socket,sys; sys.stdout.write(socket.gethostbyname(sys.argv[1]))" "$STAND_HOST")
if [ -z "$STAND_IP" ]; then
  echo "не удалось резолвнуть STAND_HOST=$STAND_HOST" >&2
  exit 1
fi

# fail-closed: политика DROP по умолчанию, потом точечный ACCEPT только на
# резолвнутый IP:port стенда (плюс loopback и ответный трафик уже
# установленных соединений).
iptables -P OUTPUT DROP
iptables -P INPUT DROP
iptables -F
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -p tcp -d "$STAND_IP" --dport "$STAND_PORT" -j ACCEPT
iptables -A INPUT -p tcp -m state --state ESTABLISHED,RELATED -j ACCEPT
ip6tables -P OUTPUT DROP 2>/dev/null || true
ip6tables -P INPUT DROP 2>/dev/null || true

export STAND_URL="${STAND_SCHEME}://${STAND_IP}:${STAND_PORT}"
exec python3 -m scenario_emulator.sandbox_relay
