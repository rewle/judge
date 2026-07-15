"""Хостовая обвязка сендбокса: запускает relay-контейнер (см.
docker/relay.Dockerfile, scenario_emulator/sandbox_relay.py) и отдаёт
объект с тем же интерфейсом `.call()`, что DirectStandClient — раннер
(scenario_emulator/runner.py) не знает, ушёл ли запрос напрямую или через
контейнер. См. docs/09_scenario_emulator.md, "Sandbox стенда для Фазы 1".

НЕ ПРОВЕРЕНО ЖИВЫМ ПРОГОНОМ на момент реализации (Docker daemon был
недоступен) — iptables-правила внутри relay-контейнера нужно перепроверить
на реальном daemon, прежде чем полагаться на этот механизм как на
настоящую границу сетевой изоляции, а не просто "похоже на изоляцию в коде".
"""
import json
import socket
import subprocess
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from scenario_emulator.stand_client import StandCallError, StandCallResult

_REPO_ROOT = Path(__file__).parent.parent
_RELAY_IMAGE = "judge-scenario-relay"


class SandboxDockerError(RuntimeError):
    """Сбой на уровне докер-сендбокса (сборка образа, запуск/остановка
    контейнера) — не транспортный сбой отдельного запроса к стенду (тот —
    StandCallError, см. scenario_emulator/stand_client.py)."""


def _resolve_stand_host(host: str) -> str:
    # host.docker.internal резолвится САМИМ контейнером при старте (см.
    # docker/relay-entrypoint.sh) — единственное имя, которое сендбоксу
    # разрешено резолвить самостоятельно, поскольку это служебное DNS-имя
    # Docker Desktop, а не что-то, на что мог бы повлиять стенд. С хоста
    # оно, как правило, не резолвится (не зарегистрировано вне докера).
    # Любой другой хост резолвим здесь, на хосте, чтобы контейнер получил
    # уже готовый IP и не делал DNS сам.
    if host in ("localhost", "127.0.0.1"):
        return "host.docker.internal"
    return socket.gethostbyname(host)


def build_relay_image():
    proc = subprocess.run(
        [
            "docker", "build", "-t", _RELAY_IMAGE,
            "-f", str(_REPO_ROOT / "docker" / "relay.Dockerfile"),
            str(_REPO_ROOT),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SandboxDockerError(f"docker build relay-образа упал:\n{proc.stderr[-2000:]}")


class SandboxedStandClient:
    def __init__(self, stand_url: str):
        parsed = urlparse(stand_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SandboxDockerError(f"stand_url должен быть http(s)://host:port, получено: {stand_url}")
        stand_host = _resolve_stand_host(parsed.hostname)
        stand_port = str(parsed.port or (443 if parsed.scheme == "https" else 80))

        build_relay_image()
        try:
            self._proc = subprocess.Popen(
                [
                    "docker", "run", "--rm", "-i",
                    "--cap-add=NET_ADMIN", "--cap-add=NET_RAW",
                    "-e", f"STAND_HOST={stand_host}",
                    "-e", f"STAND_PORT={stand_port}",
                    "-e", f"STAND_SCHEME={parsed.scheme}",
                    _RELAY_IMAGE,
                ],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except OSError as e:
            raise SandboxDockerError(f"не удалось запустить relay-контейнер: {e}") from e
        self._lock = threading.Lock()

    def call(self, method: str, path: str, body: Optional[dict] = None) -> StandCallResult:
        with self._lock:
            if self._proc.poll() is not None:
                raise StandCallError(
                    f"relay-контейнер завершился преждевременно: {self._proc.stderr.read()[-2000:]}"
                )
            req = {"method": method, "path": path, "body": body}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                raise StandCallError(
                    f"relay-контейнер не ответил: {self._proc.stderr.read()[-2000:]}"
                )
            out = json.loads(line)
        if "error" in out:
            raise StandCallError(f"relay: {out['error']}")
        return StandCallResult(status=out["status"], body=out["body"])

    def close(self):
        if self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
