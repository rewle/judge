# Relay-контейнер сендбокса — см. docs/09_scenario_emulator.md, "Sandbox
# стенда для Фазы 1", scenario_emulator/sandbox_relay.py,
# docker/relay-entrypoint.sh. НЕ ПРОВЕРЕНО живым прогоном (Docker daemon
# был недоступен на момент реализации) — требует верификации перед тем,
# как полагаться на него как на настоящую границу изоляции.
#
# Собирать из корня репозитория: docker build -t judge-scenario-relay -f docker/relay.Dockerfile .
FROM python:3.12-alpine

RUN apk add --no-cache iptables ip6tables

WORKDIR /app
COPY scenario_emulator/__init__.py scenario_emulator/stand_client.py scenario_emulator/sandbox_relay.py scenario_emulator/
COPY docker/relay-entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

# NET_ADMIN/NET_RAW добавляются раннером при docker run (нужны, чтобы
# entrypoint мог выставить iptables-правила) — не в самом образе.
ENTRYPOINT ["/app/entrypoint.sh"]
