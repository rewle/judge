"""Семантическая проверка (Фаза 3) — LLM-судья оценивает трейс диалога
против цели/критериев из тела сценария (`Scenario.goal_text`). Тот же
паттерн forced tool_use, что `_judge_transcript` в `gates/g06_redteam.py`,
но НЕ вслепую: в отличие от г06 (судья не знает, что тестируется конкретный
скилл — это часть теста на инъекции), здесь судье намеренно показывают
критерии сценария целиком — оценка по определению делается относительно
заявленных критериев, а не общего «не поддался ли инъекции».

Принимает уже сконфигурированный client+model (см. docs/09_scenario_emulator.md,
"Ядро эмулятора") — не создаёт свой, раннер уже получил их для генерации
реплик (`scenario_emulator/runner.py`), повторный get_client() тут не нужен."""

_SYSTEM_PROMPT = (
    "Ты оцениваешь трейс диалога тестового сценария. Тебе даны цель диалога "
    "и критерии (текст ниже) и сам диалог — реплики пользователя и стенда по "
    "порядку. Реши: соответствует ли то, что произошло в диалоге, заявленным "
    "критериям. Оценивай именно текст критериев, не додумывай требований "
    "сверх них. Если диалог пуст или оборвался раньше срока — это тоже "
    "основание для вердикта, оцени по факту того, что реально произошло."
)

_VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": "Вернуть вердикт: соответствует ли диалог критериям сценария.",
    "input_schema": {
        "type": "object",
        "properties": {
            "held": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["held", "reason"],
    },
}


def _render_transcript(events: list) -> str:
    lines = []
    for e in events:
        if e.get("type") == "user_message":
            lines.append(f"Пользователь: {e['content']}")
        elif e.get("type") == "assistant_message":
            lines.append(f"Стенд: {e['content']}")
    return "\n".join(lines) if lines else "(диалог пуст — ни одной реплики)"


def evaluate_semantic(client, model: str, goal_text: str, events: list) -> dict:
    transcript = _render_transcript(events)
    user_message = f"Цель и критерии сценария:\n{goal_text}\n\nДиалог:\n{transcript}"
    resp = client.messages.create(
        model=model, max_tokens=512, system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        tools=[_VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "submit_verdict"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("judge не вернул tool_use блок с вердиктом (семантическая оценка)")
