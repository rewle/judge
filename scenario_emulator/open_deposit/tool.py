"""Держатель истины паттерна детерминированного подтверждения — реестр
выданных `confirmation_id` и фактически открытых вкладов. См.
docs/09_scenario_emulator.md, аддендум "Расширение контракта:
widget-подтверждение и compose-стенды", и
scenario_emulator/open_deposit/harness.py (зовёт этот сервис, не наоборот).

Открывает вклад, только если пришедший confirmation_id реально
зарегистрирован (через /register) и ещё не использован — это и есть
"стейт был/не был вызыван и сверять" из исходной постановки. Ни один путь,
кроме POST /open_deposit с валидным confirmation_id, до _DEPOSITS не
дотягивается: opened_via в ответе всегда "widget_confirm"."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REGISTRY: dict[str, dict] = {}  # confirmation_id -> {session_id, details, used}
_DEPOSITS: dict[str, dict] = {}  # session_id -> {status, opened_via, details}


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

    def do_GET(self):
        parts = [p for p in self.path.split("/") if p]
        if parts == ["health"]:
            self._send_json(200, {"ok": True})
            return
        if len(parts) == 2 and parts[0] == "deposit":
            session_id = parts[1]
            deposit = _DEPOSITS.get(session_id)
            if deposit is None:
                self._send_json(200, {"status": "none"})
                return
            self._send_json(200, deposit)
            return
        self._send_json(404, {"error": f"неизвестный хендл GET {self.path}"})

    def do_POST(self):
        parts = [p for p in self.path.split("/") if p]
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(400, {"error": "тело запроса — не валидный JSON"})
            return

        if parts == ["register"]:
            confirmation_id = body.get("confirmation_id")
            session_id = body.get("session_id")
            details = body.get("details")
            kind = body.get("kind")
            if not confirmation_id or not session_id or details is None or not kind:
                self._send_json(
                    400,
                    {"error": "register требует confirmation_id, session_id, details и kind"},
                )
                return
            _REGISTRY[confirmation_id] = {
                "session_id": session_id, "details": details, "kind": kind, "used": False,
            }
            self._send_json(200, {"registered": True})
            return

        if parts == ["open_deposit"]:
            confirmation_id = body.get("confirmation_id")
            session_id = body.get("session_id")
            entry = _REGISTRY.get(confirmation_id)
            if entry is None:
                self._send_json(404, {"error": "unknown confirmation_id"})
                return
            if entry["used"]:
                self._send_json(409, {"error": "confirmation_id already used"})
                return
            if entry["session_id"] != session_id:
                self._send_json(409, {"error": "confirmation_id belongs to another session"})
                return
            kind = entry.get("kind", "open_deposit")
            if kind == "close_deposit":
                deposit = _DEPOSITS.get(session_id)
                if deposit is None or deposit.get("status") != "opened":
                    self._send_json(409, {"error": "no open deposit to close"})
                    return
                entry["used"] = True
                deposit["status"] = "closed"
                self._send_json(200, {"deposit": deposit, "operation": kind})
                return
            if kind == "top_up_deposit":
                deposit = _DEPOSITS.get(session_id)
                if deposit is None or deposit.get("status") != "opened":
                    self._send_json(409, {"error": "no open deposit to top up"})
                    return
                amount = entry["details"].get("top_up_amount")
                if not isinstance(amount, int) or amount <= 0:
                    self._send_json(409, {"error": "top_up_amount отсутствует или некорректен"})
                    return
                entry["used"] = True
                deposit["details"]["amount"] += amount
                self._send_json(200, {"deposit": deposit, "operation": kind})
                return
            entry["used"] = True
            deposit = {
                "status": "opened", "opened_via": "widget_confirm",
                "details": entry["details"],
            }
            _DEPOSITS[session_id] = deposit
            self._send_json(200, {"deposit": deposit, "operation": kind})
            return

        self._send_json(404, {"error": f"неизвестный хендл POST {self.path}"})


def create_server(host: str = "0.0.0.0", port: int = 8769) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"open-deposit-tool слушает http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
