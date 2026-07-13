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
import importlib
import sys
from pathlib import Path

import yaml

from gates.base import FAIL, NOT_CONFIGURED, NOT_IMPLEMENTED, SKIPPED

ROOT = Path(__file__).parent


def load_config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def run_skill(skill_path: Path, config: dict) -> dict:
    results = []
    for gate_cfg in config["gates"]:
        if not gate_cfg.get("enabled", True):
            continue
        module = importlib.import_module(gate_cfg["module"])
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
        "--check-chains",
        action="store_true",
        help="прогнать гейт 07 (граф uses:) вместо последовательной цепочки 01-06 — "
        "реестровая проверка, см. --registry",
    )
    parser.add_argument(
        "--registry",
        help="реестр для --check-chains (по умолчанию — paths.registry_dir из config.yaml)",
    )
    parser.add_argument(
        "--skip-chains",
        action="store_true",
        help="не запускать гейт 07 автоматически после полного прогона реестра",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    config = load_config()

    if args.check_chains:
        registry_dir = (
            Path(args.registry).expanduser() if args.registry else ROOT / config["paths"]["registry_dir"]
        )
        results = run_chains(registry_dir)
        any_fail = print_chains_report(results)
        sys.exit(1 if any_fail else 0)

    skills_dir = ROOT / config["paths"]["skills_dir"]

    full_registry_run = not args.path and not args.skill
    if args.path:
        skill_dirs = [Path(args.path).expanduser()]
    elif args.skill:
        skill_dirs = [skills_dir / args.skill]
    else:
        skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())

    any_fail = False
    for skill_dir in skill_dirs:
        results = run_skill(skill_dir, config)
        print_report(skill_dir.name, results)
        if any(r.status == FAIL for _, r in results):
            any_fail = True

    if full_registry_run and not args.skip_chains:
        if any_fail:
            print("\n=== 07_chain пропущен: не все скиллы реестра прошли 01-06 ===")
        else:
            registry_dir = ROOT / config["paths"]["registry_dir"]
            chain_results = run_chains(registry_dir)
            if print_chains_report(chain_results):
                any_fail = True

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
