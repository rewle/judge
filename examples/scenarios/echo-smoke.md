---
stand_url: http://localhost:8765
max_turns: 3
exact_checks:
  - type: message_contains
    role: assistant
    pattern: "^echo: "
  - type: event_count
    event: stand_call
    handle: send_message
    min: 3
    max: 3
---
Цель диалога: смоук-тест механизма эмуляции (Фаза 1,
docs/TODO_scenario_emulator.md) против фейкового эхо-стенда
(`scenario_emulator/echo_stand.py`) — не про содержательный диалог.

Веди короткий, естественный диалог из нескольких реплик на любую
нейтральную тему (например поприветствуй стенд и задай пару простых
вопросов). Стенд отвечает эхом ("echo: ...") — это ожидаемое поведение
фикстуры, не баг.

Семантически проверить (Фаза 3, пока не реализовано): реплики пользователя
связные и не пустые.
