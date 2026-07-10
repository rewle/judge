"""Гейт 04 — рубрика судьи-модели (LLM-as-judge). См. docs/04_rubric.md.

Рубрика и метод стабильности (8 параллельных прогонов, mean/sigma
run-уровня) перенесены из проверенной методологии
~/Projects/archive/skill_eval_dlq.ipynb (см. память reference-skill-eval).
Отличия от ноутбука:
  - добавлен критерий groundedness (ловит уверенно поданные непроверяемые
    факты и инструкции придумывать данные вместо проверки)
  - вместо парсинга JSON регэкспом из свободного текста — forced tool-use
    с input_schema, ответ уже провалидированная структура
  - история прогонов (history/rubric_runs.jsonl) + non-blocking сигнал
    дрифта judge-оценки той же связки skill+model+backend со временем
"""
import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL, NOT_CONFIGURED
from gates.g01_static import _read_skill
from judge_client import get_client, JudgeNotConfigured

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
RUBRIC_PATH = Path(__file__).parent.parent / "policies" / "rubric_criteria.yaml"
HISTORY_PATH = Path(__file__).parent.parent / "history" / "rubric_runs.jsonl"


def _load_prior_run(skill_name: str, model: str, backend: str) -> dict:
    """Последняя по времени запись для той же связки skill+model+backend —
    сравнивать api и cli между собой некорректно (разные пути получения
    структурированного ответа), поэтому backend тоже часть ключа."""
    if not HISTORY_PATH.exists():
        return None
    latest = None
    with HISTORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("skill") == skill_name and rec.get("model") == model and rec.get("backend") == backend:
                if latest is None or rec["timestamp"] > latest["timestamp"]:
                    latest = rec
    return latest


def _append_run(record: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    backend = getattr(client, "backend_name", "api")

    frontmatter, _, skill_text = _read_skill(skill_path)
    if skill_text is None:
        return GateResult(FAIL, "SKILL.md не найден", {})
    skill_name = (frontmatter or {}).get("name") or skill_path.name

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
        "backend": backend,
    }

    # Дрифт — сигнал, не гейт: не блокирует цепочку (нет откалиброванного
    # порога, за которым дрифт значим, а не шум), только виден в отчёте.
    prior = _load_prior_run(skill_name, model, backend)
    drift_note = ""
    if prior is not None:
        drift = round(overall_mean - prior["overall_mean"], 3)
        details["drift_vs_previous"] = drift
        details["previous_run"] = {"timestamp": prior["timestamp"], "overall_mean": prior["overall_mean"]}
        if abs(drift) >= thresholds.get("rubric_drift_warn_abs", 0.1):
            drift_note = (
                f" [дрифт {drift:+.3f} vs прогона {prior['timestamp'][:10]} "
                f"({prior['overall_mean']:.3f}) — проверить вручную]"
            )

    _append_run(
        {
            "skill": skill_name,
            "model": model,
            "backend": backend,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_mean": round(overall_mean, 3),
            "overall_sigma": round(overall_sigma, 3),
        }
    )

    if errors:
        return GateResult(FAIL, "; ".join(errors) + drift_note, details)
    return GateResult(
        PASS, f"рубрика: {overall_mean:.2f} ± {overall_sigma:.3f} (модель {model}){drift_note}", details
    )
