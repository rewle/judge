# Образ для стенда open-deposit (см. scenario_emulator/open_deposit/,
# docker/open-deposit-compose.yml) — три сервиса (tool/harness/ui-gateway)
# из одного образа, команда задаётся per-service в compose.
#
# Собирать из корня репозитория: docker build -t judge-open-deposit-stand -f docker/open_deposit.Dockerfile .
FROM python:3.12-alpine
WORKDIR /app
COPY scenario_emulator/open_deposit/*.py /app/
