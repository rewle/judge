"""Гейт 05 — эффективность по токенам (behavioral: baseline vs со скиллом).
НЕ РЕАЛИЗОВАН: требует прогона поведенческого теста дважды и сравнения
токенов/латентности с приростом coverage. См. docs/05_token_efficiency.md."""
from pathlib import Path

from gates.base import GateResult, NOT_IMPLEMENTED


def check(skill_path: Path) -> GateResult:
    return GateResult(
        NOT_IMPLEMENTED,
        "требует behavioral-теста baseline/with-skill и сравнения токенов с приростом coverage",
        {"see": "docs/05_token_efficiency.md"},
    )
