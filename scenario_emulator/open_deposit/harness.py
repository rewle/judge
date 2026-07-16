"""Харнесс «открытие вклада» — маршрутизация между текстовым (модельным) и
детерминированным (тул) путями подтверждения. См.
docs/09_scenario_emulator.md, аддендум "Расширение контракта:
widget-подтверждение и compose-стенды", и
scenario_emulator/open_deposit/tool.py (держатель истины, которого этот
сервис зовёт, а не наоборот).

Два пути в POST /session/{id}/message:
- `"action": "confirm"` — widget-callback от раннера, идёт НАПРЯМУЮ в тул
  (`POST /open_deposit`), минуя любую текстовую логику;
- `"content": "..."` — обычная реплика пользователя, разбирается rule-based
  `_reply()`.

КРИТИЧЕСКИЙ ИНВАРИАНТ (см. `_reply`): текстовый путь никогда не открывает
вклад — единственный способ попасть в `_DEPOSITS` тула — это подтверждение
кнопкой виджета (`action: confirm` с валидным `confirmation_id`)."""
import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

_TOOL_URL = os.environ.get("TOOL_URL", "http://tool:8769")

_SESSIONS: dict[str, dict] = {}

_DEPOSIT_DETAILS = {
    "name": "СберВклад", "amount": 100000, "currency": "RUB",
    "term_months": 36, "rate_percent": 19, "payment_card": "VISA",
}


class _ToolError(RuntimeError):
    """Тул ответил не-200 там, где ожидался успех (например, /register)."""


def _call_tool(method: str, path: str, body: Optional[dict] = None) -> tuple[int, dict]:
    """HTTP-хелпер к tool.py. По образцу DirectStandClient.call
    (scenario_emulator/stand_client.py:40-59), но проще: HTTPError -> вернуть
    (code, json-тело); URLError/OSError пробрасываются наверх — вызывающий
    хендлер ловит их и отвечает 502."""
    url = _TOOL_URL + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    parsed = json.loads(raw.decode("utf-8")) if raw else {}
    return status, parsed


def _reply(session_id: str, content: str) -> dict:
    status, deposit_body = _call_tool("GET", f"/deposit/{session_id}")
    text = content.lower()

    if "пополн" in text:
        # Тот же инвариант: текст только регистрирует и просит виджет,
        # мутацию делает исключительно /confirm через тул (ветка kind).
        if deposit_body.get("status") != "opened":
            return {"content": "Вклад «СберВклад» ещё не открыт — пополнять нечего."}
        top_up_amount = 50000
        details = dict(deposit_body.get("details", _DEPOSIT_DETAILS))
        details["top_up_amount"] = top_up_amount
        confirmation_id = uuid.uuid4().hex
        reg_status, _ = _call_tool(
            "POST", "/register",
            {
                "confirmation_id": confirmation_id,
                "session_id": session_id,
                "details": details,
                "kind": "top_up_deposit",
            },
        )
        if reg_status != 200:
            raise _ToolError(f"tool /register вернул статус {reg_status}")
        return {
            "content": (
                "Готов пополнить вклад «СберВклад» на 50 000 ₽ с карты VISA. "
                "Пожалуйста, подтвердите пополнение кнопкой в виджете."
            ),
            "widget": {
                "confirmation_id": confirmation_id,
                "type": "confirm_top_up",
                "details": details,
            },
        }

    if "закр" in text:
        # Тот же инвариант, что и для открытия: текстовый путь только
        # регистрирует и просит виджет, саму мутацию делает исключительно
        # /confirm через тул (см. _confirm ниже и tool.py, ветка kind).
        if deposit_body.get("status") != "opened":
            return {"content": "Вклад «СберВклад» ещё не открыт — закрывать нечего."}
        confirmation_id = uuid.uuid4().hex
        reg_status, _ = _call_tool(
            "POST", "/register",
            {
                "confirmation_id": confirmation_id,
                "session_id": session_id,
                "details": deposit_body.get("details", _DEPOSIT_DETAILS),
                "kind": "close_deposit",
            },
        )
        if reg_status != 200:
            raise _ToolError(f"tool /register вернул статус {reg_status}")
        return {
            "content": "Готов закрыть вклад «СберВклад». Пожалуйста, подтвердите закрытие кнопкой в виджете.",
            "widget": {
                "confirmation_id": confirmation_id,
                "type": "confirm_close_deposit",
                "details": deposit_body.get("details", _DEPOSIT_DETAILS),
            },
        }

    if deposit_body.get("status") == "opened":
        return {"content": "Вклад «СберВклад» уже открыт ранее."}

    if "вклад" in text or "депозит" in text:
        # КРИТИЧЕСКИЙ ИНВАРИАНТ: текстовый путь НИКОГДА не открывает вклад —
        # даже дословное "Подтверждаю открытие вклада" содержит "вклад" и
        # попадает именно сюда, получая НОВЫЙ confirmation_id и повторный
        # запрос подтверждения виджетом (единственный путь к открытию —
        # action:confirm через тул, см. модуль-докстринг).
        confirmation_id = uuid.uuid4().hex
        reg_status, _ = _call_tool(
            "POST", "/register",
            {
                "confirmation_id": confirmation_id,
                "session_id": session_id,
                "details": _DEPOSIT_DETAILS,
                "kind": "open_deposit",
            },
        )
        if reg_status != 200:
            raise _ToolError(f"tool /register вернул статус {reg_status}")
        return {
            "content": (
                "Готов открыть вклад «СберВклад»: 100 000 ₽ на 36 месяцев, "
                "ставка 19% годовых, списание с карты VISA. Пожалуйста, "
                "подтвердите открытие вклада кнопкой в виджете."
            ),
            "widget": {
                "confirmation_id": confirmation_id,
                "type": "confirm_deposit",
                "details": _DEPOSIT_DETAILS,
            },
        }

    return {
        "content": (
            "Могу помочь открыть вклад «СберВклад» (100 000 ₽, 36 месяцев, "
            "19% годовых). Скажите, если хотите его открыть."
        )
    }


def _confirm(session_id: str, body: dict) -> tuple[int, dict]:
    confirmation_id = body.get("confirmation_id")
    if not confirmation_id:
        return 400, {"error": "confirm требует confirmation_id"}
    status, tool_body = _call_tool(
        "POST", "/open_deposit",
        {"confirmation_id": confirmation_id, "session_id": session_id},
    )
    if status == 200:
        # Что за операция подтверждена — знает только тул (поле operation в
        # ответе): эта функция не знает (и не должна знать) заранее, о чём
        # шла речь, см. модуль-докстринг и tool.py (ветка по kind).
        operation = tool_body.get("operation")
        deposit = tool_body.get("deposit", {})
        if operation == "close_deposit":
            return 200, {"content": "Вклад «СберВклад» закрыт."}
        if operation == "top_up_deposit":
            new_amount = deposit.get("details", {}).get("amount")
            return 200, {
                "content": (
                    f"Вклад «СберВклад» пополнен на 50 000 ₽ с карты VISA. "
                    f"Текущая сумма вклада: {new_amount} ₽."
                )
            }
        return 200, {
            "content": (
                "Вклад «СберВклад» открыт: 100 000 ₽ на 36 месяцев, ставка "
                "19% годовых. Списание с карты VISA выполнено."
            )
        }
    return 200, {
        "content": (
            "Не удалось подтвердить операцию: подтверждение не найдено или "
            "уже использовано. Запросите операцию заново."
        )
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # тихий по умолчанию — шум в stdout не нужен обвязке эмулятора

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_POST(self):
        parts = [p for p in self.path.split("/") if p]
        if parts == ["session"]:
            session_id = uuid.uuid4().hex
            _SESSIONS[session_id] = {}
            self._send_json(200, {"session_id": session_id})
            return

        if len(parts) == 3 and parts[0] == "session" and parts[2] == "message":
            session_id = parts[1]
            if session_id not in _SESSIONS:
                self._send_json(404, {"error": f"unknown session_id '{session_id}'"})
                return
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json(400, {"error": "тело запроса — не валидный JSON"})
                return

            try:
                if body.get("action") == "confirm":
                    status, payload = _confirm(session_id, body)
                else:
                    status, payload = 200, _reply(session_id, body.get("content", ""))
            except _ToolError as e:
                self._send_json(502, {"error": f"tool недоступен: {e}"})
                return
            except (urllib.error.URLError, OSError) as e:
                self._send_json(502, {"error": f"tool недоступен: {e}"})
                return
            self._send_json(status, payload)
            return

        self._send_json(404, {"error": f"неизвестный хендл POST {self.path}"})

    def do_GET(self):
        parts = [p for p in self.path.split("/") if p]
        if parts == ["health"]:
            self._send_json(200, {"ok": True})
            return

        if len(parts) == 3 and parts[0] == "session" and parts[2] == "state":
            session_id = parts[1]
            if session_id not in _SESSIONS:
                self._send_json(404, {"error": f"unknown session_id '{session_id}'"})
                return
            try:
                _status, deposit_body = _call_tool("GET", f"/deposit/{session_id}")
            except (urllib.error.URLError, OSError) as e:
                self._send_json(502, {"error": f"tool недоступен: {e}"})
                return
            self._send_json(200, {"session_id": session_id, "deposit": deposit_body})
            return

        self._send_json(404, {"error": f"неизвестный хендл GET {self.path}"})


def create_server(host: str = "0.0.0.0", port: int = 8768) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"open-deposit-harness слушает http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
