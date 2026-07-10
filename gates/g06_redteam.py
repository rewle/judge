"""Гейт 06 — устойчивость к атакам (prompt injection, расширение прав).
НЕ РЕАЛИЗОВАН: требует библиотеки атак и второго судьи "вслепую".
См. docs/06_redteam.md."""
from pathlib import Path

from gates.base import GateResult, NOT_IMPLEMENTED


def check(skill_path: Path) -> GateResult:
    return GateResult(
        NOT_IMPLEMENTED,
        "требует адверсариальной библиотеки атак + judge вслепую",
        {"see": "docs/06_redteam.md"},
    )
