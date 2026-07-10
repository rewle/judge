"""Гейт 03 — дублирование. v0: эвристика по пересечению слов (детерминированная,
без внешних вызовов). См. docs/03_duplication.md про план перехода на embeddings."""
import re
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
STOPWORDS = {"и", "в", "на", "с", "по", "для", "не", "из", "или", "как", "the", "a", "to", "of", "and"}


def _tokenize(text: str) -> set:
    words = re.findall(r"[а-яa-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check(skill_path: Path, registry_dir: Path = None) -> GateResult:
    frontmatter, body, text = _read_skill(skill_path)
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter", {})

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    threshold = config["thresholds"]["duplication_similarity"]
    registry_dir = registry_dir or (Path(__file__).parent.parent / config["paths"]["registry_dir"])

    own_tokens = _tokenize((frontmatter.get("description", "") or "") + " " + (body or ""))

    matches = []
    for other_dir in sorted(registry_dir.iterdir()):
        if not other_dir.is_dir() or other_dir.resolve() == skill_path.resolve():
            continue
        other_fm, other_body, other_text = _read_skill(other_dir)
        if other_fm is None:
            continue
        other_tokens = _tokenize((other_fm.get("description", "") or "") + " " + (other_body or ""))
        sim = _jaccard(own_tokens, other_tokens)
        if sim >= threshold:
            matches.append((other_dir.name, round(sim, 3)))

    if matches:
        details_str = ", ".join(f"{name} (sim={sim})" for name, sim in matches)
        return GateResult(
            FAIL,
            f"похож на существующие скиллы: {details_str} (порог {threshold})",
            {"matches": matches},
        )
    return GateResult(PASS, "дублей выше порога не найдено (v0: word-overlap эвристика)", {})
