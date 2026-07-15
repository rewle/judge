"""Фейковый стенд «закрытие счёта» — реалистичный пример для
scenario_emulator (в отличие от echo_stand.py, у которого нет бизнес-логики
вообще). См. scenario_emulator/README.md, "Примеры", и
examples/scenarios/close-account.md.

Одна захардкоженная сессия на счёт: реалистичного мультиаккаунтового стенда
здесь не нужно, задача файла — дать сценарию нетривиальное состояние
(open → pending_confirmation → closed) и повод для exact_checks/семантики,
а не быть образцовым банковским бэкендом.

Реализует тот же контракт из трёх хендлов, что echo_stand.py: POST
/session, POST /session/{id}/message, GET /session/{id}/state."""
import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SESSIONS: dict[str, dict] = {}

_ACCOUNT_ID = "123"
_BALANCE = 4200.0
_COMMISSION_RATE = 1.5  # % от остатка

_CONFIRM_WORDS = ("подтвержда", "соглас", "закрой", "да,", "да ")


def _commission() -> float:
    return round(_BALANCE * _COMMISSION_RATE / 100, 2)


def _reply(session: dict, content: str) -> str:
    text = content.lower()
    account = session["account"]
    commission = _commission()

    if account["status"] == "open" and ("закры" in text or "закрыть" in text):
        account["status"] = "pending_confirmation"
        return (
            f"Закрытие счёта {_ACCOUNT_ID} потребует комиссию {commission}₽ "
            f"({_COMMISSION_RATE}% от остатка {_BALANCE}₽). Уже проведённые "
            f"платежи отменить или вернуть нельзя. Подтвердите закрытие."
        )
    if "комисси" in text:
        return (
            f"Комиссия за закрытие счёта {_ACCOUNT_ID} — {_COMMISSION_RATE}% "
            f"от остатка, сейчас это {commission}₽."
        )
    if account["status"] == "pending_confirmation" and any(w in text for w in _CONFIRM_WORDS):
        account["status"] = "closed"
        return f"Счёт {_ACCOUNT_ID} закрыт, комиссия {commission}₽ списана."
    return f"Уточните, пожалуйста: закрыть счёт {_ACCOUNT_ID} или узнать про комиссию?"


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
            _SESSIONS[session_id] = {
                "account": {
                    "id": _ACCOUNT_ID,
                    "status": "open",
                    "balance": _BALANCE,
                    "commission_rate": _COMMISSION_RATE,
                },
            }
            self._send_json(200, {"session_id": session_id})
            return
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "message":
            session_id = parts[1]
            session = _SESSIONS.get(session_id)
            if session is None:
                self._send_json(404, {"error": f"unknown session_id '{session_id}'"})
                return
            try:
                body = self._read_json_body()
            except json.JSONDecodeError:
                self._send_json(400, {"error": "тело запроса — не валидный JSON"})
                return
            reply = _reply(session, body.get("content", ""))
            self._send_json(200, {"content": reply})
            return
        self._send_json(404, {"error": f"неизвестный хендл POST {self.path}"})

    def do_GET(self):
        parts = [p for p in self.path.split("/") if p]
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "state":
            session_id = parts[1]
            session = _SESSIONS.get(session_id)
            if session is None:
                self._send_json(404, {"error": f"unknown session_id '{session_id}'"})
                return
            self._send_json(200, {"session_id": session_id, "account": session["account"]})
            return
        self._send_json(404, {"error": f"неизвестный хендл GET {self.path}"})


def create_server(host: str = "127.0.0.1", port: int = 8766) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"close-account-stand слушает http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
