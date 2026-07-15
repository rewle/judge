"""Ядро эмулятора — цикл диалога эмулированного пользователя со стендом.
См. docs/09_scenario_emulator.md, разделы "Ядро эмулятора" и "Формат
трейса"/"Формат отчёта".

Каждый ход — один вызов judge_client (по умолчанию cli-бэкенд, решение 4:
"api-бэкенд не требуется"): единственное user-сообщение, в которое целиком
сериализован накопленный диалог, а не история из нескольких сообщений в
`messages` (cli-бэкенд её не поддерживает, см. judge_client.py:100-103).

Критерий остановки — только max_turns (детерминированно, решение от
2026-07-15): раннер не спрашивает модель, достигнута ли цель, это увело бы
раннер (Фаза 1) от семантической проверки (Фаза 3)."""
import os
from pathlib import Path

import yaml

from judge_client import get_client, JudgeNotConfigured
from scenario_emulator.checks import CheckSpecError, all_passed, evaluate_checks
from scenario_emulator.scenario import Scenario
from scenario_emulator.semantic import evaluate_semantic
from scenario_emulator.stand_client import StandCallError
from scenario_emulator.trace import TraceWriter, read_trace

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

_SYSTEM_PROMPT_TEMPLATE = """Ты играешь роль пользователя в диалоге с внешней системой («стендом»), \
которая тестируется этим сценарием. Твоя цель и критерии поведения:

{goal_text}

Правила:
- На каждом шаге дай РОВНО ОДНУ следующую реплику от лица пользователя — \
только текст реплики, без кавычек, пояснений и рассуждений вслух.
- Учитывай весь предыдущий диалог, который тебе покажут в сообщении.
- Лимит хода в этом прогоне — {max_turns} реплик пользователя. Не пытайся \
сам определить, что диалог завершён, и не сигнализируй об этом — просто \
веди диалог естественно к цели, прогон остановит раннер."""


def _build_user_prompt(transcript: list[dict], turn: int, max_turns: int) -> str:
    if not transcript:
        return (
            f"Диалог ещё не начинался. Это ход 1 из {max_turns}. Напиши "
            f"открывающую реплику пользователя."
        )
    lines = [
        f"{'Пользователь' if e['role'] == 'user' else 'Стенд'}: {e['content']}"
        for e in transcript
    ]
    history = "\n".join(lines)
    return (
        f"Диалог до сих пор:\n{history}\n\n"
        f"Это ход {turn + 1} из {max_turns}. Напиши следующую реплику "
        f"пользователя. Ответ — только текст реплики."
    )


def _extract_text(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _judge_model() -> str:
    config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return os.environ.get("JUDGE_MODEL", config.get("judge", {}).get("model", "claude-sonnet-5"))


class _StandFailure(Exception):
    """Внутренний контроль потока — сворачивает диалог досрочно (транспортный
    сбой или не-200 от стенда), но НЕ пропускает exact_checks: даже частичный
    трейс интересно свериться (например event_count честно покажет, что
    диалог оборвался раньше ожидаемого)."""


def run_scenario(scenario: Scenario, stand_client, out_dir: Path) -> dict:
    """stand_client — DirectStandClient или SandboxedStandClient (одинаковый
    интерфейс .call(), см. scenario_emulator/stand_client.py). exact_checks
    (Фаза 2) и semantic (Фаза 3) считаются всегда, по факту записанного
    трейса — даже если run_status != "ok" (частичный трейс всё равно
    интересно свериться и оценить, см. _StandFailure)."""
    os.environ.setdefault("JUDGE_BACKEND", "cli")  # решение 4: api не требуется
    try:
        judge = get_client()
    except JudgeNotConfigured as e:
        raise RuntimeError(f"judge недоступен: {e}") from e
    model = _judge_model()

    trace_path = out_dir / "trace.jsonl"
    report = {
        "scenario": scenario.path.stem,
        "stand_url": scenario.stand_url,
        "run_status": "ok",
        "exact_checks": None,
        "exact_passed": None,
        "semantic": None,
        "trace_path": str(trace_path),
    }

    try:
        _run_dialogue(scenario, stand_client, judge, model, trace_path)
    except _StandFailure:
        report["run_status"] = "stand_error"

    _finish_report(report, scenario, judge, model)
    return report


def _run_dialogue(scenario: Scenario, stand_client, judge, model: str, trace_path: Path) -> None:
    with TraceWriter(trace_path) as trace:
        try:
            open_result = stand_client.call("POST", "/session")
        except StandCallError as e:
            trace.write("stand_call", handle="open_session", request={}, error=str(e))
            raise _StandFailure from e
        trace.write(
            "stand_call", handle="open_session", request={},
            response=open_result.body, status=open_result.status,
        )
        if open_result.status != 200:
            raise _StandFailure
        session_id = open_result.body.get("session_id")
        if not session_id:
            raise _StandFailure

        transcript: list[dict] = []
        for turn in range(scenario.max_turns):
            system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
                goal_text=scenario.goal_text, max_turns=scenario.max_turns,
            )
            user_prompt = _build_user_prompt(transcript, turn, scenario.max_turns)
            resp = judge.messages.create(
                model=model, max_tokens=512, system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            generated = _extract_text(resp)
            trace.write("user_message", content=generated)
            transcript.append({"role": "user", "content": generated})

            try:
                msg_result = stand_client.call(
                    "POST", f"/session/{session_id}/message", {"content": generated},
                )
            except StandCallError as e:
                trace.write(
                    "stand_call", handle="send_message",
                    request={"content": generated}, error=str(e),
                )
                raise _StandFailure from e
            trace.write(
                "stand_call", handle="send_message", request={"content": generated},
                response=msg_result.body, status=msg_result.status,
            )
            if msg_result.status != 200:
                raise _StandFailure
            reply = msg_result.body.get("content", "")
            trace.write("assistant_message", content=reply)
            transcript.append({"role": "stand", "content": reply})

        try:
            state_result = stand_client.call("GET", f"/session/{session_id}/state")
        except StandCallError as e:
            trace.write("stand_call", handle="get_state", request={}, error=str(e))
            raise _StandFailure from e
        trace.write(
            "stand_call", handle="get_state", request={},
            response=state_result.body, status=state_result.status,
        )
        if state_result.status != 200:
            raise _StandFailure


def _finish_report(report: dict, scenario: Scenario, judge, model: str) -> None:
    events = read_trace(Path(report["trace_path"]))

    if scenario.exact_checks:
        try:
            results = evaluate_checks(scenario.exact_checks, events)
        except CheckSpecError as e:
            raise RuntimeError(f"exact_checks в сценарии некорректны: {e}") from e
        report["exact_checks"] = results
        report["exact_passed"] = all_passed(results)

    # semantic — не опционален, как exact_checks: тело сценария (goal_text)
    # обязательно (Scenario.load это гарантирует), судье всегда есть что
    # оценивать, даже если диалог пуст/оборвался (см. semantic.py).
    report["semantic"] = evaluate_semantic(judge, model, scenario.goal_text, events)
