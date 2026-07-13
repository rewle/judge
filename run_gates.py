#!/usr/bin/env python3
"""Последовательный раннер гейтов. Гейты идут в порядке из config.yaml;
первый FAIL останавливает цепочку для этого скилла (следующие гейты не
запускаются). NOT_IMPLEMENTED/SKIPPED не блокируют — цепочка продолжается,
но помечается как неполная.

Гейт 07 (см. gates/g07_chain.py) не входит в последовательную per-skill
цепочку выше, потому что реестровый (видит граф uses: целиком, не один
скилл) — см. docs/roadmap_chains.md. При полном прогоне реестра (без
--skill/--path) запускается автоматически после того, как все скиллы
реестра прошли последовательную цепочку из config.yaml (--skip-chains
отключает). При точечной проверке одного скилла (--skill/--path) не
запускается — граф целиком проверять незачем. --check-chains гоняет
только гейт 07, отдельно, без остальной цепочки.
"""
import argparse
import difflib
import importlib.util
import inspect
import sys
from pathlib import Path

import yaml

from gates.base import FAIL, NOT_CONFIGURED, NOT_IMPLEMENTED, SKIPPED

ROOT = Path(__file__).parent


class ConfigError(Exception):
    """config.yaml сломан — проблема конфига (зона админа реестра), не скиллов."""


# Валидация конфига — тот же приём, что валидация политики в гейте 02
# (прогон роли «владелец системы»): без неё ошибка админа — это либо
# traceback (битый YAML, удалённый порог, опечатка в module), либо молчание
# (опечатанный порог игнорируется, гейт живёт на зашитом дефолте, и админ
# не узнаёт, что его порог не применился).
_KNOWN_TOP = ("gates", "paths", "judge", "thresholds")
_REQUIRED_TOP = ("gates", "paths", "thresholds")
_KNOWN_PATHS = ("skills_dir", "registry_dir", "external_state_root")
_REQUIRED_PATHS = ("skills_dir", "registry_dir")
_KNOWN_JUDGE = ("model", "n_runs")
_KNOWN_GATE_KEYS = ("id", "module", "enabled")
# Пороги обязательны все: часть гейтов берёт их прямой индексацией
# (KeyError при пропаже), часть — через .get с зашитым дефолтом (молчаливый
# рассинхрон конфига с фактическим поведением). Явный список — единственный
# способ поймать и то и другое на входе.
_REQUIRED_THRESHOLDS = (
    "duplication_similarity",
    "duplication_semantic_prefilter",
    "duplication_semantic_similarity",
    "rubric_min_score",
    "rubric_max_sigma",
    "rubric_max_criterion_sigma",
    "rubric_drift_warn_abs",
    "token_efficiency_max_overhead_pct",
    "token_efficiency_min_coverage_gain_pp",
    "chain_io_compat_min_score",
    "chain_token_budget_max",
)


def _line_of(raw: str, needle: str) -> str:
    for i, line in enumerate(raw.splitlines(), 1):
        if needle in line:
            return f" (строка {i})"
    return ""


def _unknown_key_errors(section: dict, known: tuple, where: str, raw: str) -> list:
    errors = []
    for key in section:
        if key in known:
            continue
        hint = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
        suffix = f" — возможно, опечатка в '{hint[0]}'" if hint else ""
        errors.append(f"неизвестный ключ '{key}' в {where}{suffix}{_line_of(raw, key)}")
    return errors


def _validate_config(config, raw: str) -> list:
    if not isinstance(config, dict):
        return [f"ожидается YAML-маппинг с ключами {', '.join(_REQUIRED_TOP)}, "
                f"а распарсился {type(config).__name__}"]

    errors = []
    for key in config:
        if key in _KNOWN_TOP:
            continue
        if key == "policies":
            errors.append(
                "секция 'policies' не читается ни одним гейтом — пути к файлам "
                "политик фиксированы (policies/*.yaml захардкожены в гейтах); "
                "убери секцию, чтобы конфиг не обещал ручку, которой нет"
                + _line_of(raw, "policies")
            )
            continue
        hint = difflib.get_close_matches(key, _KNOWN_TOP, n=1, cutoff=0.6)
        suffix = f" — возможно, опечатка в '{hint[0]}'" if hint else ""
        errors.append(f"неизвестный ключ верхнего уровня '{key}'{suffix}{_line_of(raw, key)}")

    for key in _REQUIRED_TOP:
        if key not in config:
            errors.append(f"нет обязательной секции '{key}'")
    if errors:
        return errors

    gates = config["gates"]
    if not isinstance(gates, list) or not gates:
        errors.append("'gates' должен быть непустым списком гейтов")
    else:
        seen_ids = set()
        for i, gate in enumerate(gates):
            if not isinstance(gate, dict) or "id" not in gate or "module" not in gate:
                errors.append(f"gates[{i}]: каждый гейт — маппинг с ключами id и module")
                continue
            errors.extend(_unknown_key_errors(gate, _KNOWN_GATE_KEYS, f"gates ('{gate['id']}')", raw))
            if gate["id"] in seen_ids:
                errors.append(f"дублирующийся id гейта '{gate['id']}'")
            seen_ids.add(gate["id"])
            try:
                spec = importlib.util.find_spec(gate["module"])
            except (ImportError, ValueError):
                spec = None
            if spec is None:
                errors.append(
                    f"модуль '{gate['module']}' гейта '{gate['id']}' не найден — "
                    f"опечатка?{_line_of(raw, gate['module'])}"
                )

    paths = config["paths"]
    if not isinstance(paths, dict):
        errors.append("'paths' должен быть маппингом")
    else:
        errors.extend(_unknown_key_errors(paths, _KNOWN_PATHS, "paths", raw))
        for key in _REQUIRED_PATHS:
            if key not in paths:
                errors.append(f"в paths нет обязательного ключа '{key}'")

    if isinstance(config.get("judge"), dict):
        errors.extend(_unknown_key_errors(config["judge"], _KNOWN_JUDGE, "judge", raw))

    thresholds = config["thresholds"]
    if not isinstance(thresholds, dict):
        errors.append("'thresholds' должен быть маппингом")
    else:
        errors.extend(_unknown_key_errors(thresholds, _REQUIRED_THRESHOLDS, "thresholds", raw))
        for key in _REQUIRED_THRESHOLDS:
            if key not in thresholds:
                errors.append(
                    f"в thresholds нет обязательного порога '{key}' — гейты "
                    f"без него либо падают, либо молча живут на зашитом дефолте"
                )
        for key, value in thresholds.items():
            if key in _REQUIRED_THRESHOLDS and not isinstance(value, (int, float)):
                errors.append(f"порог '{key}' должен быть числом, а не {type(value).__name__}")

    return errors


def load_config():
    raw = (ROOT / "config.yaml").read_text(encoding="utf-8")
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        loc = ""
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            loc = f" (строка {mark.line + 1}, колонка {mark.column + 1})"
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(f"config.yaml не парсится как YAML{loc}: {problem}")
    errors = _validate_config(config, raw)
    if errors:
        raise ConfigError("config.yaml сломан:\n  - " + "\n  - ".join(errors))
    return config


def run_skill(skill_path: Path, config: dict, registry_dir: Path = None) -> dict:
    results = []
    for gate_cfg in config["gates"]:
        if not gate_cfg.get("enabled", True):
            continue
        module = importlib.import_module(gate_cfg["module"])
        # Гейтам, умеющим сравнивать с реестром (03: параметр registry_dir),
        # пробрасываем реестр прогона: при --skills-dir дубли ищутся внутри
        # прогоняемой группы, а не против registry_dir из config.yaml.
        if registry_dir is not None and "registry_dir" in inspect.signature(module.check).parameters:
            result = module.check(skill_path, registry_dir=registry_dir)
        else:
            result = module.check(skill_path)
        results.append((gate_cfg["id"], result))
        if result.blocks:
            break  # fail-fast: следующие гейты не запускаются
    return results


def print_report(skill_name: str, results: list):
    print(f"\n=== {skill_name} ===")
    for gate_id, result in results:
        icon = {
            "pass": "PASS",
            "fail": "FAIL",
            "not_implemented": "SKIP(TODO)",
            "not_configured": "SKIP(CFG)",
            "skipped": "SKIP",
        }
        label = icon.get(result.status, result.status.upper())
        print(f"  [{label:10}] {gate_id}: {result.message}")
    ran_ids = {g for g, _ in results}
    all_ids = [g["id"] for g in load_config()["gates"] if g.get("enabled", True)]
    stopped_early = ran_ids != set(all_ids)
    if any(r.status == FAIL for _, r in results):
        stopped_at = next(g for g, r in results if r.status == FAIL)
        print(f"  -> цепочка остановлена на {stopped_at}")
    elif stopped_early:
        print("  -> не все гейты реализованы")
    else:
        # Явный вердикт на успехе: без него новичок видит хвост из PASS/SKIP и
        # не уверен, всё ли хорошо (особенно пугает SKIP(CFG) у judge-гейтов).
        # SKIP — это «не запускался», не «провал»: цепочку он не блокирует.
        skipped = [g for g, r in results if r.status in (SKIPPED, NOT_CONFIGURED, NOT_IMPLEMENTED)]
        verdict = "  -> блокирующих гейтов нет — скилл проходит проверку"
        if skipped:
            verdict += (
                f" (гейты {', '.join(skipped)} пропущены — не настроен judge/eval, "
                f"это не провал; см. README про JUDGE_BACKEND)"
            )
        print(verdict)


def run_chains(registry_dir: Path) -> dict:
    from gates.g07_chain import check_registry

    return check_registry(registry_dir)


def print_chains_report(results: dict):
    print(f"\n=== 07_chain (граф uses:, {len(results)} скиллов) ===")
    any_fail = False
    for name in sorted(results):
        result = results[name]
        label = "FAIL" if result.status == FAIL else result.status.upper()
        print(f"  [{label:4}] {name}: {result.message}")
        if result.status == FAIL:
            any_fail = True
    return any_fail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", help="имя одного скилла из paths.skills_dir")
    parser.add_argument(
        "--path",
        help="путь к конкретной папке скилла вне skills_dir (для локальной проверки "
        "нового скилла, см. new_skill.py); имеет приоритет над --skill",
    )
    parser.add_argument(
        "--skills-dir",
        help="прогнать полный цикл по произвольной папке со скиллами вместо "
        "paths.skills_dir из config.yaml (кейс эксперта: проверить свою группу "
        "как реестр, не правя конфиг); гейт 03 при этом ищет дубли внутри неё же, "
        "а гейт 07 берёт её как реестр, если --registry не задан",
    )
    parser.add_argument(
        "--check-policy",
        nargs="?",
        const=True,
        default=None,
        metavar="POLICY_ROOT",
        help="проверить только политику прав (policies/tools_allowlist.yaml + "
        "policies/systems/*.yaml) и config.yaml, без прогона скиллов — "
        "кейс владельца системы: быстрая проверка своей правки политики. "
        "Опциональный аргумент — корень другой политики (папка с "
        "tools_allowlist.yaml и systems/), используется фикстурами битых "
        "политик examples/policies/*",
    )
    parser.add_argument(
        "--check-chains",
        action="store_true",
        help="прогнать гейт 07 (граф uses:) вместо последовательной цепочки 01-06 — "
        "реестровая проверка, см. --registry",
    )
    parser.add_argument(
        "--registry",
        help="реестр для --check-chains и автозапуска гейта 07 (по умолчанию — "
        "--skills-dir, если задан, иначе paths.registry_dir из config.yaml)",
    )
    parser.add_argument(
        "--skip-chains",
        action="store_true",
        help="не запускать гейт 07 автоматически после полного прогона реестра",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    try:
        config = load_config()
    except ConfigError as exc:
        # Отдельный код выхода: FAIL скилла — 1, сломанный конфиг — 2
        # (в CI различимо: «скилл плохой» vs «пайплайн неисправен»).
        print(f"Проблема конфига (зона админа реестра, не автора скилла): {exc}")
        sys.exit(2)

    if args.check_policy is not None:
        from gates.g02_permissions import PolicyError, _load_policy

        policy_path = None
        if args.check_policy is not True:
            policy_path = Path(args.check_policy).expanduser() / "tools_allowlist.yaml"
        try:
            policy = _load_policy(policy_path)
        except PolicyError as exc:
            print(f"Политика сломана: {exc}")
            sys.exit(2)
        print(
            f"Политика в порядке: allowed_by_default={len(policy.get('allowed_by_default') or [])}, "
            f"requires_justification={len(policy.get('requires_justification') or [])}, "
            f"always_flag_for_review={len(policy.get('always_flag_for_review') or [])} "
            f"(глобальная + systems/*.yaml после мёрджа)"
        )
        sys.exit(0)

    if args.check_chains:
        registry_dir = (
            Path(args.registry).expanduser() if args.registry else ROOT / config["paths"]["registry_dir"]
        )
        results = run_chains(registry_dir)
        any_fail = print_chains_report(results)
        sys.exit(1 if any_fail else 0)

    override_dir = Path(args.skills_dir).expanduser() if args.skills_dir else None
    skills_dir = override_dir if override_dir else ROOT / config["paths"]["skills_dir"]

    full_registry_run = not args.path and not args.skill
    if args.path:
        skill_dirs = [Path(args.path).expanduser()]
    elif args.skill:
        skill_dirs = [skills_dir / args.skill]
    else:
        skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())

    any_fail = False
    for skill_dir in skill_dirs:
        results = run_skill(skill_dir, config, registry_dir=override_dir)
        print_report(skill_dir.name, results)
        if any(r.status == FAIL for _, r in results):
            any_fail = True

    if full_registry_run and not args.skip_chains:
        if any_fail:
            print("\n=== 07_chain пропущен: не все скиллы реестра прошли 01-06 ===")
        else:
            if args.registry:
                registry_dir = Path(args.registry).expanduser()
            elif override_dir:
                registry_dir = override_dir
            else:
                registry_dir = ROOT / config["paths"]["registry_dir"]
            chain_results = run_chains(registry_dir)
            if print_chains_report(chain_results):
                any_fail = True

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
