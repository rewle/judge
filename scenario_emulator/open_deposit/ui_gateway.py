"""Transport passthrough — единственный сервис open-deposit, чей порт
публикуется наружу (127.0.0.1:8767 в docker-compose), остальные (harness,
tool) доступны только внутри docker-сети. См.
/Users/rewle/.claude/plans/mighty-sprouting-neumann.md, раздел 3.3, и
docs/09_scenario_emulator.md, раздел "HTTP-контракт стенда".

Гейтвей НЕ разбирает тело запроса и НЕ решает, текст это или action —
просто пересылает сырые байты в UPSTREAM_URL, решение уже принял вызывающий
(раннер).

Три хендла контракта (POST /session, POST /session/{id}/message, GET
/session/{id}/state) пересылаются как есть — тот же метод, путь, тело;
статус и тело ответа возвращаются без изменений. GET /health — исключение:
на него отвечает сам гейтвей, не проксируя, это readiness-сигнал для
run_scenario.py.

GET / — тоже исключение, не часть контракта эмулятора: отдаёт статическую
demo-страницу (чат от лица человека вместо LLM-судьи) для ручной
демонстрации паттерна в браузере. Страница делает fetch() ровно к тем же
трём хендлам с этого же origin (гейтвей и так единственный порт наружу,
CORS не нужен). Кнопка «Подтвердить» на карточке виджета шлёт
{"action":"confirm","confirmation_id":...} напрямую из JS, минуя любое
текстовое поле — визуально то же самое разделение путей, что раннер
делает программно (scenario_emulator/runner.py)."""
import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "http://harness:8768")

_INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>open-deposit — демо детерминированного подтверждения</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f4f5f7; --panel: #ffffff; --text: #1a1a1a; --muted: #6b7280;
    --accent: #0a6cff; --accent-ink: #ffffff; --border: #e2e4e9;
    --bubble-user: #0a6cff; --bubble-user-ink: #ffffff;
    --bubble-stand: #eef0f4; --bubble-stand-ink: #1a1a1a;
    --widget-bg: #fff7e6; --widget-border: #f0c869;
    --confirm-bg: #17a34a; --confirm-ink: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #14161a; --panel: #1c1f26; --text: #eef0f4; --muted: #9aa2b1;
      --accent: #4d94ff; --accent-ink: #0b0d10; --border: #2b2f38;
      --bubble-user: #2f6fe0; --bubble-user-ink: #ffffff;
      --bubble-stand: #262a33; --bubble-stand-ink: #eef0f4;
      --widget-bg: #2a2412; --widget-border: #8a6a1f;
      --confirm-bg: #1f9c53; --confirm-ink: #ffffff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; justify-content: center; padding: 24px 12px;
  }
  .app { width: 100%; max-width: 640px; display: flex; flex-direction: column; gap: 12px; }
  header { text-align: center; padding: 4px 8px 8px; }
  header h1 { font-size: 17px; margin: 0 0 4px; }
  header p { margin: 0; font-size: 13px; color: var(--muted); }
  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,.04);
  }
  #chat { padding: 16px; min-height: 320px; max-height: 55vh; overflow-y: auto;
          display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; }
  .row.user { justify-content: flex-end; }
  .row.stand { justify-content: flex-start; }
  .row.system { justify-content: center; }
  .bubble { max-width: 78%; padding: 10px 14px; border-radius: 16px; font-size: 14px;
            line-height: 1.4; white-space: pre-wrap; }
  .row.user .bubble { background: var(--bubble-user); color: var(--bubble-user-ink); border-bottom-right-radius: 4px; }
  .row.stand .bubble { background: var(--bubble-stand); color: var(--bubble-stand-ink); border-bottom-left-radius: 4px; }
  .row.system .bubble { background: transparent; color: var(--muted); font-size: 12px; text-align: center; max-width: 100%; }
  .widget-card {
    margin-top: 6px; max-width: 78%; align-self: flex-start; background: var(--widget-bg);
    border: 1px solid var(--widget-border); border-radius: 14px; padding: 12px 14px; font-size: 13px;
  }
  .widget-card h3 { margin: 0 0 8px; font-size: 14px; }
  .widget-card dl { margin: 0 0 12px; display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; }
  .widget-card dt { color: var(--muted); }
  .widget-card dd { margin: 0; font-weight: 600; }
  .confirm-btn {
    width: 100%; border: none; border-radius: 10px; padding: 10px 12px;
    background: var(--confirm-bg); color: var(--confirm-ink); font-size: 14px; font-weight: 600;
    cursor: pointer;
  }
  .confirm-btn:disabled { opacity: .55; cursor: default; }
  .confirm-hint { margin-top: 8px; font-size: 11px; color: var(--muted); }
  form#composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); }
  #text {
    flex: 1; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
    font-size: 14px; background: var(--bg); color: var(--text);
  }
  #send { border: none; border-radius: 10px; padding: 10px 16px; background: var(--accent);
          color: var(--accent-ink); font-size: 14px; font-weight: 600; cursor: pointer; }
  #send:disabled, #text:disabled { opacity: .5; cursor: default; }
  details.debug { font-size: 12px; }
  details.debug summary { padding: 10px 14px; cursor: pointer; color: var(--muted); }
  #log { margin: 0; padding: 0 14px 14px; max-height: 220px; overflow-y: auto;
         font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px;
         white-space: pre-wrap; color: var(--muted); }
  #log .req { color: var(--accent); }
</style>
</head>
<body>
<div class="app">
  <header>
    <h1>«СберВклад» — демо детерминированного подтверждения</h1>
    <p>Клик по кнопке в карточке ниже шлёт action:confirm напрямую в тул — минуя модель.
       Текстовое «подтверждаю» так вклад не откроет — попробуйте сами.</p>
  </header>
  <div class="panel">
    <div id="chat"></div>
    <form id="composer">
      <input id="text" type="text" placeholder="Например: хочу открыть вклад" autocomplete="off">
      <button id="send" type="submit">Отправить</button>
    </form>
  </div>
  <div class="panel">
    <details class="debug">
      <summary>Сырые запросы/ответы (видно, что клик — не текст)</summary>
      <pre id="log">(пока пусто)</pre>
    </details>
  </div>
</div>
<script>
let sessionId = null;

const chat = document.getElementById("chat");
const log = document.getElementById("log");
const form = document.getElementById("composer");
const textInput = document.getElementById("text");
const sendBtn = document.getElementById("send");

function addRow(role, text) {
  const row = document.createElement("div");
  row.className = "row " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  return row;
}

function addWidgetCard(widget) {
  const card = document.createElement("div");
  card.className = "widget-card";
  const d = widget.details || {};
  card.innerHTML =
    "<h3>Подтверждение операции</h3>" +
    "<dl>" +
    "<dt>Вклад</dt><dd>" + (d.name ?? "") + "</dd>" +
    "<dt>Сумма</dt><dd>" + (d.amount ?? "") + " " + (d.currency ?? "") + "</dd>" +
    "<dt>Срок</dt><dd>" + (d.term_months ?? "") + " мес.</dd>" +
    "<dt>Ставка</dt><dd>" + (d.rate_percent ?? "") + "%</dd>" +
    "<dt>Списание</dt><dd>карта " + (d.payment_card ?? "") + "</dd>" +
    "</dl>";
  const btn = document.createElement("button");
  btn.className = "confirm-btn";
  btn.type = "button";
  btn.textContent = "Подтвердить";
  btn.addEventListener("click", () => confirmWidget(widget.confirmation_id, btn, card));
  card.appendChild(btn);
  const hint = document.createElement("div");
  hint.className = "confirm-hint";
  hint.textContent = "confirmation_id: " + widget.confirmation_id;
  card.appendChild(hint);
  chat.appendChild(card);
  chat.scrollTop = chat.scrollHeight;
}

function logCall(label, request, response) {
  const line = document.createElement("div");
  line.innerHTML = "<div class='req'>&gt; " + label + " " + JSON.stringify(request) + "</div>" +
                    "<div>&lt; " + JSON.stringify(response) + "</div>";
  if (log.textContent === "(пока пусто)") log.textContent = "";
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

async function callMessage(body, label) {
  const resp = await fetch("/session/" + sessionId + "/message", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const data = await resp.json();
  logCall(label, body, data);
  return data;
}

async function openSession() {
  const resp = await fetch("/session", {method: "POST"});
  const data = await resp.json();
  sessionId = data.session_id;
  logCall("POST /session", {}, data);
  addRow("system", "Сессия открыта: " + sessionId);
}

async function confirmWidget(confirmationId, btn, card) {
  btn.disabled = true;
  btn.textContent = "Отправлено...";
  addRow("user", "🔘 нажата кнопка «Подтвердить» (не текст)");
  const data = await callMessage({action: "confirm", confirmation_id: confirmationId}, "POST /message (widget-callback)");
  addRow("stand", data.content || "");
  btn.textContent = "Подтверждено";
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = textInput.value.trim();
  if (!text || !sessionId) return;
  textInput.value = "";
  sendBtn.disabled = true;
  addRow("user", text);
  const data = await callMessage({content: text}, "POST /message (текст)");
  addRow("stand", data.content || "");
  if (data.widget && data.widget.confirmation_id) {
    addWidgetCard(data.widget);
  }
  sendBtn.disabled = false;
  textInput.focus();
});

openSession();
</script>
</body>
</html>
"""


def _is_contract_path(method: str, parts: list) -> bool:
    if method == "POST" and parts == ["session"]:
        return True
    if method == "POST" and len(parts) == 3 and parts[0] == "session" and parts[2] == "message":
        return True
    if method == "GET" and len(parts) == 3 and parts[0] == "session" and parts[2] == "state":
        return True
    return False


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

    def _send_raw(self, status: int, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _forward(self, method: str, path: str, raw_body: bytes):
        """Переслать метод/путь/тело в UPSTREAM_URL как есть, вернуть
        (status, raw_body) апстрима без разбора. Транспортный сбой —
        502 отправляется вызывающим хендлом."""
        url = _UPSTREAM_URL.rstrip("/") + path
        data = raw_body if raw_body else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _handle(self, method: str):
        parts = [p for p in self.path.split("/") if p]
        if method == "GET" and parts == []:
            self._send_html(200, _INDEX_HTML)
            return
        if method == "GET" and parts == ["health"]:
            self._send_json(200, {"ok": True})
            return
        if not _is_contract_path(method, parts):
            self._send_json(404, {"error": f"неизвестный хендл {method} {self.path}"})
            return
        raw_body = self._read_raw_body()
        try:
            status, body = self._forward(method, self.path, raw_body)
        except (urllib.error.URLError, OSError) as e:
            self._send_json(502, {"error": f"harness недоступен: {e}"})
            return
        self._send_raw(status, body)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def create_server(host: str = "0.0.0.0", port: int = 8767) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"ui_gateway слушает http://{args.host}:{args.port}, upstream {_UPSTREAM_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
