"""Процесс внутри сендбокс-контейнера (Docker, сеть ограничена стендом —
docs/09_scenario_emulator.md, раздел "Sandbox стенда для Фазы 1"). Ничего,
кроме пересылки HTTP-запросов к ОДНОМУ запиненному при старте stand_url, не
делает: даже если раннер на хосте скомпрометирован входом от стенда, канал
из этого процесса физически не может уйти никуда, кроме адреса из STAND_URL
(плюс сетевая изоляция самого контейнера — этот файл только вторая,
прикладная линия защиты, не замена ей).

Протокол — построчный JSON по stdin/stdout (docker run -i, без публикации
порта): вход {"method": "...", "path": "...", "body": {...} | null},
выход {"status": int, "body": {...}} или {"error": "..."} при транспортном
сбое. Один процесс на весь прогон сценария — не один docker run на вызов,
чтобы не платить cold-start контейнера на каждый ход диалога."""
import json
import os
import sys

from scenario_emulator.stand_client import DirectStandClient, StandCallError


def main():
    stand_url = os.environ.get("STAND_URL")
    if not stand_url:
        print(json.dumps({"error": "STAND_URL не задан в окружении relay-контейнера"}))
        sys.exit(1)

    client = DirectStandClient(stand_url)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = client.call(req["method"], req["path"], req.get("body"))
            out = {"status": result.status, "body": result.body}
        except StandCallError as e:
            out = {"error": str(e)}
        except (json.JSONDecodeError, KeyError) as e:
            out = {"error": f"невалидный запрос relay-протокола: {e}"}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
