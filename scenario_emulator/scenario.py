"""Загрузка файла сценария. См. docs/09_scenario_emulator.md, раздел
"Формат сценария". Парсинг frontmatter — тот же приём split("---", 2), что
`gates/g01_static.py:_read_skill` для SKILL.md."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ScenarioError(ValueError):
    """Сценарий отсутствует или не проходит минимальную валидацию формы."""


@dataclass
class Scenario:
    path: Path
    stand_url: str
    max_turns: int
    exact_checks: list = field(default_factory=list)
    goal_text: str = ""  # тело после frontmatter — свободный текст, в judge не парсится (Фаза 3)


def load_scenario(path: Path) -> Scenario:
    if not path.is_file():
        raise ScenarioError(f"файл сценария не найден: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ScenarioError(f"{path}: нет frontmatter (--- ... ---) в начале файла")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ScenarioError(f"{path}: блок --- ... --- не закрыт")
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        raise ScenarioError(f"{path}: невалидный YAML во frontmatter: {e}") from e

    stand_url = frontmatter.get("stand_url")
    if not stand_url:
        raise ScenarioError(f"{path}: frontmatter обязан содержать stand_url")
    max_turns = frontmatter.get("max_turns")
    if not isinstance(max_turns, int) or max_turns < 1:
        raise ScenarioError(f"{path}: frontmatter обязан содержать max_turns (целое, >= 1)")
    exact_checks = frontmatter.get("exact_checks", []) or []

    goal_text = parts[2].strip()
    if not goal_text:
        raise ScenarioError(f"{path}: тело сценария (цель диалога) пустое")

    return Scenario(
        path=path,
        stand_url=stand_url,
        max_turns=max_turns,
        exact_checks=exact_checks,
        goal_text=goal_text,
    )
