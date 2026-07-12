"""Гейт 08 — ссылки на внешние файлы (skill->file, направление B роадмапа
цепочек). Детерминированный, per-skill, без judge. См. docs/08_file_refs.md
и docs/roadmap_chains.md, раздел "Направление B".

Скиллы в целевых системах могут читать/писать файлы общей базы знаний вне
самого реестра скиллов (шаблоны, конфиги, датасеты) — эти ссылки не входят
в граф uses: (это не другой скилл) и гейт 07 их не видит. Здесь — узкая
эвристика: путь к файлу, упомянутый в теле сразу после триггерной фразы
('прочитай', 'read', 'по шаблону из' и т.п.), должен резолвиться
относительно paths.external_state_root.

Без paths.external_state_root в конфиге гейт SKIPPED целиком — конвенция
новая, внедрение постепенное (тот же принцип, что eval.yaml у гейта 05).
"""
import re
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL, SKIPPED
from gates.g01_static import _read_skill

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# Узкая эвристика намеренно (как _mentioned_skill_names в гейте 07):
# требует триггерную фразу И расширение файла в токене — минимизирует
# ложные срабатывания ценой пропуска путей без явного расширения или без
# триггерного слова рядом (например "см. также X" без "прочитай").
_TRIGGER_RE = re.compile(
    r"(?:прочита[йть]+|read|по\s+шаблону\s+из|template\s+from|из\s+файла|from\s+file)\s+"
    r"`?([^\s`\"']+\.[A-Za-z0-9]{1,5})`?",
    re.IGNORECASE,
)


def _extract_file_refs(body: str) -> list:
    # Вне fenced code-блоков — тот же приём, что в гейтах 01/07, чтобы не
    # ловить примеры вида "read `/some/path.md`" внутри демонстрационного
    # кода как реальную ссылку.
    prose = re.sub(r"```.*?```", "", body or "", flags=re.DOTALL)
    return [m.group(1) for m in _TRIGGER_RE.finditer(prose)]


def check(skill_path: Path, external_state_root: Path = None) -> GateResult:
    frontmatter, body, text = _read_skill(skill_path)
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter", {})

    if external_state_root is None:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        root = config.get("paths", {}).get("external_state_root")
        if not root:
            return GateResult(
                SKIPPED,
                "paths.external_state_root не задан — проверка ссылок на внешние файлы пропущена",
                {},
            )
        external_state_root = Path(__file__).parent.parent / root

    refs = _extract_file_refs(body or "")
    if not refs:
        return GateResult(PASS, "ссылок на внешние файлы в теле не найдено", {})

    # Ссылка обязана оставаться внутри external_state_root после резолва:
    # абсолютный путь или ../-выход за корень — FAIL независимо от того,
    # существует ли файл (иначе гейт можно использовать как пробу чужой
    # файловой системы, а скилл — привязать к файлу вне общей базы знаний).
    root_resolved = external_state_root.resolve()
    missing, outside = [], []
    for ref in refs:
        target = (external_state_root / ref).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            outside.append(ref)
        elif not target.exists():
            missing.append(ref)
    details = {"refs": refs, "missing": missing, "outside_root": outside, "external_state_root": str(external_state_root)}
    if missing or outside:
        parts = []
        if outside:
            parts.append(f"ссылки выходят за пределы {external_state_root}: {', '.join(outside)}")
        if missing:
            parts.append(f"ссылки на несуществующие файлы (относительно {external_state_root}): {', '.join(missing)}")
        return GateResult(FAIL, "; ".join(parts), details)
    return GateResult(PASS, f"{len(refs)} ссылок на внешние файлы, все резолвятся", details)
