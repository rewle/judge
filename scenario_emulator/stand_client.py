"""HTTP-клиент к стенду. См. docs/09_scenario_emulator.md, раздел
"HTTP-контракт стенда".

Один примитив `call(method, path, body)` вместо трёх отдельных методов —
намеренно: раннер (scenario_emulator/runner.py) сам знает семантику каждого
хендла (open_session/send_message/get_state) и пишет соответствующее
trace-событие; `call()` одинаков для прямого клиента (без сети-изоляции,
локальная разработка) и сендбоксированного (через relay-контейнер,
scenario_emulator/docker_sandbox.py) — раннеру не нужно знать, каким путём
ушёл запрос.

Не-200 ответ стенда — не исключение, а обычный результат (см. документ:
решает раннер, не транспорт). Исключение — только транспортный сбой
(нет соединения, таймаут, не-JSON тело)."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


class StandCallError(RuntimeError):
    """Транспортный сбой при обращении к стенду (не путать с не-200 ответом)."""


@dataclass
class StandCallResult:
    status: int
    body: dict


class DirectStandClient:
    """Без сети-изоляции — прямой HTTP к stand_url. Для локальной разработки
    и для случая --no-sandbox (см. run_scenario.py)."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, body: Optional[dict] = None) -> StandCallResult:
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read()
            status = e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise StandCallError(f"{method} {url}: {e}") from e
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as e:
            raise StandCallError(f"{method} {url}: стенд вернул не-JSON тело: {e}") from e
        return StandCallResult(status=status, body=parsed)
