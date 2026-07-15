#!/usr/bin/env python3
"""CLI-обвязка эмулятора пользователя по декларативному сценарию. См.
docs/09_scenario_emulator.md. Не гейт из config.yaml/run_gates.py —
отдельный инструмент (решение 8 в docs/TODO_scenario_emulator.md).

Фазы 1 (диалог со стендом → трейс), 2 (точные проверки трейса) и 3
(семантическая оценка через judge) сделаны. exact_checks/exact_passed
заполнены, только если в сценарии вообще были exact_checks (иначе null —
нечего проверять); semantic заполняется всегда — тело сценария (цель и
критерии) обязательно, судье есть что оценить в любом случае.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scenario_emulator.runner import run_scenario
from scenario_emulator.scenario import ScenarioError, load_scenario
from scenario_emulator.stand_client import DirectStandClient

REPO_ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path, help="путь к файлу сценария")
    parser.add_argument(
        "--out-dir", type=Path, default=REPO_ROOT / "runs",
        help="куда писать trace.jsonl/report.json прогона (по умолчанию ./runs)",
    )
    parser.add_argument(
        "--no-sandbox", action="store_true",
        help="прямой HTTP к стенду, без сети-изоляции (решение 5) — только "
             "для локальной разработки, НЕ для прогона против недоверенного стенда",
    )
    args = parser.parse_args()

    try:
        scenario = load_scenario(args.scenario)
    except ScenarioError as e:
        print(f"ошибка сценария: {e}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out_dir / f"{scenario.path.stem}_{timestamp}"

    stand_client = None
    try:
        if args.no_sandbox:
            stand_client = DirectStandClient(scenario.stand_url)
        else:
            from scenario_emulator.docker_sandbox import SandboxDockerError, SandboxedStandClient
            try:
                stand_client = SandboxedStandClient(scenario.stand_url)
            except SandboxDockerError as e:
                print(
                    f"сендбокс недоступен: {e}\n"
                    f"для локальной обкатки без сети-изоляции используй --no-sandbox",
                    file=sys.stderr,
                )
                sys.exit(1)

        try:
            report = run_scenario(scenario, stand_client, run_dir)
        except RuntimeError as e:
            print(f"прогон не запустился: {e}", file=sys.stderr)
            sys.exit(1)
    finally:
        if stand_client is not None and hasattr(stand_client, "close"):
            stand_client.close()

    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    semantic_held = report["semantic"]["held"] if report["semantic"] else True
    ok = report["run_status"] == "ok" and report["exact_passed"] is not False and semantic_held
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
