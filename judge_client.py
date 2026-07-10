"""Общий клиент для LLM-судьи (гейты 04-06). Два бэкенда:

api (по умолчанию) — настоящий Anthropic API, платный ключ:
  JUDGE_API_KEY / ANTHROPIC_API_KEY — ключ (обязателен для этого бэкенда)
  JUDGE_BASE_URL                    — опционально, для своего прокси
                                       (например корпоративного) вместо
                                       прямого api.anthropic.com

cli — локальный `claude -p` (Claude Code CLI), без платного API-ключа,
  через ту же авторизацию, что и интерактивная сессия (подписка/OAuth).
  Только для локальной обкатки гейтов — см. README и ClaudeCLIClient ниже.
  JUDGE_BACKEND=cli     — включает этот бэкенд
  JUDGE_CLI_BINARY      — опционально, путь к бинарнику (по умолчанию "claude")

Креды нигде не хранятся в репозитории, только через env.
"""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import anthropic


class JudgeNotConfigured(RuntimeError):
    """Судья недоступен по инфраструктурным причинам: нет ключа, не выбран
    бэкенд, или конкретный бэкенд не умеет то, что от него просят (например
    cli не умеет count_tokens)."""


class JudgeBackendError(RuntimeError):
    """Судья был доступен, но конкретный вызов сломался (ошибка процесса,
    невалидный вывод и т.п.) — в отличие от JudgeNotConfigured, это не
    ожидаемое состояние, а сбой, который стоит явно увидеть."""


class _CLIMessages:
    """Адаптер под интерфейс anthropic.Anthropic().messages: реализует
    .create() и .count_tokens() с той же сигнатурой вызова, что используют
    gates/g04_rubric.py, gates/g06_redteam.py и behavioral.py — благодаря
    этому сами гейты не знают, какой бэкенд под капотом.

    Поддерживает только одно user-сообщение без истории (текущим гейтам
    больше не нужно) и не более одного forced tool за вызов.
    """

    def __init__(self, binary: str):
        self._binary = binary

    def create(self, model, max_tokens, system, messages, tools=None, tool_choice=None):
        if len(messages) != 1 or messages[0].get("role") != "user":
            raise JudgeBackendError(
                "CLI-бэкенд поддерживает только один user-message без истории диалога"
            )
        if tools and len(tools) > 1:
            raise JudgeBackendError("CLI-бэкенд поддерживает не более одного forced tool за вызов")

        cmd = [
            self._binary, "-p", messages[0]["content"],
            "--output-format", "json",
            "--no-session-persistence",
            "--tools", "",
            "--model", model,
            "--system-prompt", system,
        ]

        tool_name = None
        if tools:
            tool_name = tools[0]["name"]
            cmd += ["--json-schema", json.dumps(tools[0]["input_schema"])]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as e:
            raise JudgeBackendError(f"claude -p не ответил за 300с: {e}") from e

        if proc.returncode != 0:
            raise JudgeBackendError(
                f"claude -p завершился с кодом {proc.returncode}: {proc.stderr.strip()[:500]}"
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise JudgeBackendError(f"не удалось распарсить JSON-вывод claude -p: {e}") from e

        if data.get("is_error"):
            raise JudgeBackendError(f"claude -p вернул ошибку: {data.get('result')}")

        if tool_name is not None:
            structured = data.get("structured_output")
            if structured is None:
                raise JudgeBackendError(
                    "claude -p не вернул structured_output несмотря на --json-schema"
                )
            content = [SimpleNamespace(type="tool_use", input=structured, name=tool_name)]
        else:
            content = [SimpleNamespace(type="text", text=data.get("result", ""))]
        return SimpleNamespace(content=content)

    def count_tokens(self, model, system, messages):
        # claude -p не даёт offline-эндпоинт подсчёта токенов без генерации —
        # приблизительная оценка обесценила бы весь смысл гейта 05 (точный
        # overhead без траты токенов на генерацию). Гейт 05 требует api-бэкенд.
        raise JudgeNotConfigured(
            "CLI-бэкенд не поддерживает count_tokens — гейт 05 требует api-бэкенд "
            "(JUDGE_API_KEY/ANTHROPIC_API_KEY), CLI годится только для 04/06."
        )


class ClaudeCLIClient:
    """Судья через локальный Claude Code CLI вместо платного API-ключа.
    Использует ту же авторизацию, что и текущая интерактивная сессия
    (подписка/OAuth), поэтому не годится для CI (там нужен настоящий ключ
    и предсказуемая параллельность/латентность) — только для локальной
    обкатки цепочки без ключа. См. README, раздел 'Настройка судьи'."""

    backend_name = "cli"

    def __init__(self, binary: str = "claude"):
        self.messages = _CLIMessages(binary)


def get_client():
    backend = os.environ.get("JUDGE_BACKEND", "api").lower()

    if backend == "cli":
        binary = os.environ.get("JUDGE_CLI_BINARY", "claude")
        if shutil.which(binary) is None:
            raise JudgeNotConfigured(
                f"JUDGE_BACKEND=cli, но бинарник '{binary}' не найден в PATH — "
                "нужен установленный Claude Code CLI (npm i -g @anthropic-ai/claude-code)."
            )
        return ClaudeCLIClient(binary)

    if backend != "api":
        raise JudgeNotConfigured(
            f"неизвестный JUDGE_BACKEND='{backend}' — допустимые значения: api, cli"
        )

    api_key = os.environ.get("JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise JudgeNotConfigured(
            "не задан JUDGE_API_KEY / ANTHROPIC_API_KEY — гейт требует ключ Anthropic API "
            "(или JUDGE_BASE_URL на совместимый прокси), либо JUDGE_BACKEND=cli для "
            "локального прогона через Claude Code CLI без ключа. См. README."
        )
    kwargs = {"api_key": api_key}
    base_url = os.environ.get("JUDGE_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)
