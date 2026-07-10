"""Гейт 04 — рубрика судьи-модели (6 критериев + groundedness) + сигма по 8 прогонам.
НЕ РЕАЛИЗОВАН: требует вызова LLM-судьи (см. docs/04_rubric.md и методологию
в памяти reference-skill-eval / ~/Projects/archive/skill_eval_dlq.ipynb).
Интерфейс зафиксирован, чтобы run_gates.py уже сейчас мог его подключить."""
from pathlib import Path

from gates.base import GateResult, NOT_IMPLEMENTED


def check(skill_path: Path) -> GateResult:
    return GateResult(
        NOT_IMPLEMENTED,
        "требует LLM-judge (рубрика из 6 критериев + groundedness, 8 прогонов на sigma)",
        {"see": "docs/04_rubric.md"},
    )
