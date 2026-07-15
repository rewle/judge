"""Фейковый эхо-стенд для обкатки эмулятора (Фаза 1). См.
docs/09_scenario_emulator.md, раздел "HTTP-контракт стенда".

Реализует ровно три хендла минимального контракта, ничего больше:
POST /session, POST /session/{id}/message, GET /session/{id}/state.
Держит сессии в памяти процесса (решение 3 — "держит сессию в фоне,
пересылает сообщения"); при перезапуске процесса сессии теряются, это
осознанно нормально для фейкового стенда обкатки.

Ответ на сообщение — просто эхо с префиксом, без всякой бизнес-логики:
цель этого стенда не сама переписка, а проверка механизма эмуляции
(сценарий → диалог → трейс) до подключения настоящих внешних систем.
"""
import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SESSIONS: dict[str, dict] = {}


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
            _SESSIONS[session_id] = {"messages": []}
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
            content = body.get("content", "")
            session["messages"].append({"role": "user", "content": content})
            reply = f"echo: {content}"
            session["messages"].append({"role": "stand", "content": reply})
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
            self._send_json(200, {
                "session_id": session_id,
                "turn_count": len(session["messages"]) // 2,
                "messages": session["messages"],
            })
            return
        self._send_json(404, {"error": f"неизвестный хендл GET {self.path}"})


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"echo-stand слушает http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
