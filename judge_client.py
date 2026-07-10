"""Общий клиент для LLM-судьи (гейты 04-06). Конфигурация только через
переменные окружения — креды не хранятся в репозитории.

JUDGE_API_KEY / ANTHROPIC_API_KEY — ключ (обязателен)
JUDGE_BASE_URL                    — опционально, для своего прокси
                                     (например корпоративного) вместо
                                     прямого api.anthropic.com
"""
import os

import anthropic


class JudgeNotConfigured(RuntimeError):
    pass


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise JudgeNotConfigured(
            "не задан JUDGE_API_KEY / ANTHROPIC_API_KEY — гейт требует ключ Anthropic API "
            "(или JUDGE_BASE_URL на совместимый прокси). См. README, раздел 'Настройка судьи'."
        )
    kwargs = {"api_key": api_key}
    base_url = os.environ.get("JUDGE_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)
