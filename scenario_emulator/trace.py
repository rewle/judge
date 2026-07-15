"""Запись трейса прогона сценария. См. docs/09_scenario_emulator.md, раздел
"Формат трейса" — append-only JSONL, по конвенции history/rubric_runs.jsonl
(единственный уже существующий precedent append-only лога в проекте, но
трейс живёт в runs/, не в history/ — см. документ, почему)."""
import json
from datetime import datetime, timezone
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, event_type: str, **fields):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **fields,
        }
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def read_trace(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
