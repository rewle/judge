"""Гейт 02 — права/MCP-scope. Детерминированный. См. docs/02_permissions.md."""
import fnmatch
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill

POLICY_PATH = Path(__file__).parent.parent / "policies" / "tools_allowlist.yaml"


def _load_policy():
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def check(skill_path: Path) -> GateResult:
    frontmatter, _, text = _read_skill(skill_path)
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter — гейт 01 должен был отсеять раньше", {})

    policy = _load_policy()
    allowed = set(policy.get("allowed_by_default", []))
    needs_just = set(policy.get("requires_justification", []))
    always_flag = policy.get("always_flag_for_review", [])

    tools = frontmatter.get("tools", []) or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",")]

    justification = (frontmatter.get("justification") or "").strip()

    errors = []
    flagged = []

    for tool in tools:
        for pattern in always_flag:
            if fnmatch.fnmatch(tool, pattern):
                flagged.append((tool, pattern))

        if tool in allowed:
            continue
        base_tool = tool.split("(")[0]
        if base_tool in needs_just:
            if not justification:
                errors.append(
                    f"инструмент '{tool}' требует поля 'justification' в frontmatter"
                )
        else:
            errors.append(f"инструмент '{tool}' не в allowlist и не в списке requires_justification")

    if flagged:
        details_str = ", ".join(f"{t} ~ {p}" for t, p in flagged)
        errors.append(f"требует ручного ревью (гейт 06): {details_str}")

    if errors:
        return GateResult(FAIL, "; ".join(errors), {"errors": errors, "flagged": flagged})
    return GateResult(PASS, "права скилла соответствуют политике", {})
