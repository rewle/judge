"""Общая логика поведенческого теста (используется гейтами 05 и в перспективе
06): прогон агента с/без скилла, judge-оценка покрытия ожидаемого поведения,
подсчёт входных токенов без реальной генерации. Переиспользует
judge_client.get_client(). Coverage-judge — тот же паттерн forced tool-use,
что и в гейте 04 (см. gates/g04_rubric.py), не regex-парсинг свободного
текста, как в ноутбуке-первоисточнике.
"""
from typing import List, Tuple

DEFAULT_AGENT_SYS = "Ты ассистент, выполняющий задачу пользователя точно и по существу."

COV_SYS = (
    "Ты строгий проверяющий. Для каждого ожидаемого поведения реши, "
    "проявлено ли оно в ответе. Ты не знаешь, чем сгенерирован ответ."
)

_COVERAGE_TOOL = {
    "name": "submit_coverage",
    "description": "Вернуть по каждому ожидаемому поведению: проявлено ли оно в ответе.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "met": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["n", "met", "evidence"],
                },
            }
        },
        "required": ["items"],
    },
}


def run_agent(client, model: str, system: str, user: str, max_tokens: int = 1024) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def judge_coverage(client, model: str, expected_behavior: List[str], answer: str) -> Tuple[float, dict]:
    eb = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(expected_behavior))
    user = f"ОЖИДАЕМЫЕ ПОВЕДЕНИЯ:\n{eb}\n\nОТВЕТ:\n<<<\n{answer}\n>>>"
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=COV_SYS,
        messages=[{"role": "user", "content": user}],
        tools=[_COVERAGE_TOOL],
        tool_choice={"type": "tool", "name": "submit_coverage"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            items = block.input["items"]
            met = sum(1 for x in items if x["met"])
            return met / len(expected_behavior), block.input
    raise RuntimeError("judge не вернул tool_use блок с покрытием")


def count_input_tokens(client, model: str, system: str, user: str) -> int:
    """Считает входные токены без реальной генерации (messages.count_tokens)."""
    r = client.messages.count_tokens(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.input_tokens
