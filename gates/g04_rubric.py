"""Гейт 04 — рубрика судьи-модели (LLM-as-judge). См. docs/04_rubric.md.

Рубрика и метод стабильности (8 параллельных прогонов, mean/sigma
run-уровня) перенесены из проверенной методологии
~/Projects/archive/skill_eval_dlq.ipynb (см. память reference-skill-eval).
Отличия от ноутбука:
  - добавлен критерий groundedness (ловит уверенно поданные непроверяемые
    факты и инструкции придумывать данные вместо проверки)
  - вместо парсинга JSON регэкспом из свободного текста — forced tool-use
    с input_schema, ответ уже провалидированная структура
"""
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL, NOT_CONFIGURED
from gates.g01_static import _read_skill
from judge_client import get_client, JudgeNotConfigured

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
RUBRIC_PATH = Path(__file__).parent.parent / "policies" / "rubric_criteria.yaml"


def _load_rubric() -> dict:
    return yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))


JUDGE_SYS = (
    "Ты оцениваешь skill-документ по официальным критериям авторинга скиллов Anthropic. "
    "Ты не знаешь автора и не знаешь, хороший он или плохой. Оцени честно."
)

def _build_tool_schema(rubric: dict) -> dict:
    return {
        "name": "submit_rubric_scores",
        "description": "Вернуть оценку по каждому критерию рубрики.",
        "input_schema": {
            "type": "object",
            "properties": {
                crit: {
                    "type": "object",
                    "properties": {
                        "score": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "reason"],
                }
                for crit in rubric
            },
            "required": list(rubric.keys()),
        },
    }


def _judge_once(client, model: str, skill_text: str, rubric: dict, tool_schema: dict) -> dict:
    crit = "\n".join(f"- {k}: {v}" for k, v in rubric.items())
    user = f"Критерии (каждый оцени числом 0.0-1.0):\n{crit}\n\nСКИЛЛ:\n<<<\n{skill_text}\n>>>"
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=JUDGE_SYS,
        messages=[{"role": "user", "content": user}],
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": "submit_rubric_scores"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("судья не вернул tool_use блок с оценками")


def check(skill_path: Path) -> GateResult:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    judge_cfg = config.get("judge", {})
    n_runs = judge_cfg.get("n_runs", 8)
    model = os.environ.get("JUDGE_MODEL", judge_cfg.get("model", "claude-sonnet-5"))

    try:
        client = get_client()
    except JudgeNotConfigured as e:
        return GateResult(NOT_CONFIGURED, str(e), {})

    _, _, skill_text = _read_skill(skill_path)
    if skill_text is None:
        return GateResult(FAIL, "SKILL.md не найден", {})

    rubric = _load_rubric()
    tool_schema = _build_tool_schema(rubric)

    with ThreadPoolExecutor(max_workers=n_runs) as ex:
        runs = list(
            ex.map(lambda _: _judge_once(client, model, skill_text, rubric, tool_schema), range(n_runs))
        )

    # Стабильность считается на уровне усреднённого по критериям балла
    # каждого прогона (как в ноутбуке-первоисточнике), а не сигмой по
    # отдельным критериям — это то, что там же откалибровано порогом 0.02.
    run_scores = [statistics.mean(v["score"] for v in run.values()) for run in runs]
    overall_mean = statistics.mean(run_scores)
    overall_sigma = statistics.pstdev(run_scores)

    per_criterion = {
        crit: round(statistics.mean(run[crit]["score"] for run in runs), 3) for crit in rubric
    }

    thresholds = config["thresholds"]
    errors = []
    if overall_mean < thresholds["rubric_min_score"]:
        errors.append(f"средний балл {overall_mean:.3f} ниже порога {thresholds['rubric_min_score']}")
    if overall_sigma > thresholds["rubric_max_sigma"]:
        errors.append(
            f"sigma {overall_sigma:.3f} выше порога {thresholds['rubric_max_sigma']} — судья нестабилен"
        )

    details = {
        "per_criterion": per_criterion,
        "overall_mean": round(overall_mean, 3),
        "overall_sigma": round(overall_sigma, 3),
        "run_scores": [round(s, 3) for s in run_scores],
        "model": model,
    }

    if errors:
        return GateResult(FAIL, "; ".join(errors), details)
    return GateResult(PASS, f"рубрика: {overall_mean:.2f} ± {overall_sigma:.3f} (модель {model})", details)
