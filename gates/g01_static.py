"""Гейт 01 — статические метрики. Детерминированный. См. docs/01_static.md."""
import re
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
BANNED_NAME_WORDS = ("anthropic", "claude")
TRIGGER_WORDS = ("когда", "use when", "whenever", "использовать когда")
MAX_DESCRIPTION_LEN = 1024
MAX_BODY_LINES = 500


def _read_skill(skill_path: Path):
    md_files = list(skill_path.glob("SKILL.md"))
    if not md_files:
        return None, None, None
    text = md_files[0].read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, None, text
    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2]
    return frontmatter, body, text


def check(skill_path: Path) -> GateResult:
    frontmatter, body, text = _read_skill(skill_path)
    if text is None:
        return GateResult(FAIL, "SKILL.md не найден", {})
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter (--- ... ---) в начале файла", {})

    errors = []

    name = frontmatter.get("name", "")
    if not NAME_RE.match(name):
        errors.append(f"name '{name}' не kebab-case / длиннее 64 симв.")
    if any(w in name.lower() for w in BANNED_NAME_WORDS):
        errors.append(f"name '{name}' содержит запрещённое слово (anthropic/claude)")

    description = frontmatter.get("description", "")
    if not description:
        errors.append("description пустой")
    elif len(description) > MAX_DESCRIPTION_LEN:
        errors.append(f"description длиннее {MAX_DESCRIPTION_LEN} символов")
    if description and not any(t in description.lower() for t in TRIGGER_WORDS):
        errors.append("description не содержит триггера использования (когда/use when/...)")

    body_lines = (body or "").splitlines()
    if len(body_lines) > MAX_BODY_LINES:
        errors.append(f"тело скилла длиннее {MAX_BODY_LINES} строк ({len(body_lines)})")

    # Пути с обратным слешем ищем вне code span'ов/блоков — иначе ложно
    # ловим regex-примеры вида `\d{2}\.\d{2}` в документации (см. golden-skill).
    prose = re.sub(r"```.*?```", "", body or "", flags=re.DOTALL)
    prose = re.sub(r"`[^`]*`", "", prose)
    if re.search(r"[a-zA-Z]:\\|\.\\\w|\\\\", prose):
        errors.append("в теле (вне code span'ов) найдены пути с обратным слешем — использовать только /")

    if errors:
        return GateResult(FAIL, "; ".join(errors), {"errors": errors})
    return GateResult(PASS, "статические метрики в норме", {})
