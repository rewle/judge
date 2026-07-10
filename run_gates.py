#!/usr/bin/env python3
"""Последовательный раннер гейтов. Гейты идут в порядке из config.yaml;
первый FAIL останавливает цепочку для этого скилла (следующие гейты не
запускаются). NOT_IMPLEMENTED/SKIPPED не блокируют — цепочка продолжается,
но помечается как неполная.
"""
import argparse
import importlib
import sys
from pathlib import Path

import yaml

from gates.base import FAIL, NOT_IMPLEMENTED, SKIPPED

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
        icon = {"pass": "PASS", "fail": "FAIL", "not_implemented": "SKIP(TODO)", "skipped": "SKIP"}
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", help="имя одного скилла из paths.skills_dir")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    config = load_config()
    skills_dir = ROOT / config["paths"]["skills_dir"]

    if args.skill:
        skill_dirs = [skills_dir / args.skill]
    else:
        skill_dirs = sorted(d for d in skills_dir.iterdir() if d.is_dir())

    any_fail = False
    for skill_dir in skill_dirs:
        results = run_skill(skill_dir, config)
        print_report(skill_dir.name, results)
        if any(r.status == FAIL for _, r in results):
            any_fail = True

    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
