"""Гейт 05 — эффективность по токенам (behavioral: baseline vs со скиллом).
См. docs/05_token_efficiency.md.

Логика: сначала дёшево (без генерации) считаем overhead входных токенов
(messages.count_tokens). Если он в пределах порога — PASS без дальнейших
трат. Если выше порога — обязаны обосновать его реальным приростом coverage
(два прогона агента + judge покрытия, как в gate 04/behavioral.py), иначе
FAIL: скилл платит токенами за задачу, которую модель и так решает.
"""
import os
from pathlib import Path

import yaml

from behavioral import DEFAULT_AGENT_SYS, count_input_tokens, judge_coverage, run_agent
from gates.base import FAIL, NOT_CONFIGURED, PASS, SKIPPED, GateResult
from gates.g01_static import _read_skill
from judge_client import JudgeNotConfigured, get_client

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def check(skill_path: Path) -> GateResult:
    eval_path = skill_path / "eval.yaml"
    if not eval_path.exists():
        return GateResult(SKIPPED, "нет eval.yaml — поведенческий тест для скилла не задан", {})

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    judge_cfg = config.get("judge", {})
    model = os.environ.get("JUDGE_MODEL", judge_cfg.get("model", "claude-sonnet-5"))

    try:
        client = get_client()
    except JudgeNotConfigured as e:
        return GateResult(NOT_CONFIGURED, str(e), {})

    eval_spec = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    query = eval_spec["query"]
    expected_behavior = eval_spec["expected_behavior"]
    input_data = eval_spec.get("input", "")
    agent_sys = eval_spec.get("agent_system_prompt", DEFAULT_AGENT_SYS)

    _, _, skill_text = _read_skill(skill_path)

    baseline_user = f"{input_data}\n\nЗАДАЧА:\n{query}".strip()
    with_skill_user = f"СКИЛЛ:\n{skill_text}\n\n{input_data}\n\nЗАДАЧА:\n{query}".strip()

    baseline_tokens = count_input_tokens(client, model, agent_sys, baseline_user)
    with_skill_tokens = count_input_tokens(client, model, agent_sys, with_skill_user)
    overhead_pct = (
        (with_skill_tokens - baseline_tokens) / baseline_tokens * 100 if baseline_tokens else 0.0
    )

    thresholds = config["thresholds"]
    max_overhead = thresholds["token_efficiency_max_overhead_pct"]
    min_gain = thresholds.get("token_efficiency_min_coverage_gain_pp", 10)

    details = {
        "baseline_tokens": baseline_tokens,
        "with_skill_tokens": with_skill_tokens,
        "overhead_pct": round(overhead_pct, 1),
    }

    if overhead_pct <= max_overhead:
        details["note"] = "overhead в пределах порога — coverage не проверялся (не требуется для решения)"
        return GateResult(
            PASS, f"overhead токенов {overhead_pct:.0f}% в пределах порога {max_overhead}%", details
        )

    # overhead выше порога — обязаны обосновать его приростом coverage
    baseline_answer = run_agent(client, model, agent_sys, baseline_user)
    with_skill_answer = run_agent(client, model, agent_sys, with_skill_user)
    baseline_cov, _ = judge_coverage(client, model, expected_behavior, baseline_answer)
    with_skill_cov, _ = judge_coverage(client, model, expected_behavior, with_skill_answer)
    gain_pp = (with_skill_cov - baseline_cov) * 100

    details.update(
        {
            "baseline_coverage": round(baseline_cov, 3),
            "with_skill_coverage": round(with_skill_cov, 3),
            "coverage_gain_pp": round(gain_pp, 1),
        }
    )

    if gain_pp < min_gain:
        return GateResult(
            FAIL,
            f"overhead токенов {overhead_pct:.0f}% выше порога {max_overhead}%, а прирост "
            f"coverage +{gain_pp:.0f}п.п. не оправдывает его (нужно ≥{min_gain}п.п.)",
            details,
        )
    return GateResult(
        PASS,
        f"overhead токенов {overhead_pct:.0f}% выше порога, но оправдан приростом coverage +{gain_pp:.0f}п.п.",
        details,
    )
