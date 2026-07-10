# judge

CI/CD-гейты для реестра скиллов (Claude Code skills) и конфигов. Каждый PR со
скиллом проходит цепочку гейтов **последовательно**: фейл гейта останавливает
проверку — следующие гейты не запускаются.

## Гейты (в порядке прогона)

| # | Гейт | Детерминизм | Что проверяет | Статус |
|---|---|---|---|---|
| 01 | [static](docs/01_static.md) | детерминированный | frontmatter, длина, пути | реализован |
| 02 | [permissions](docs/02_permissions.md) | детерминированный | tools/MCP против allowlist | реализован |
| 03 | [duplication](docs/03_duplication.md) | детерминированный (v0: эвристика) | пересечение с существующими скиллами | v0-заглушка |
| 04 | [rubric](docs/04_rubric.md) | недетерминированный (LLM-judge) | 6 критериев качества + groundedness | реализован, требует ключ |
| 05 | [token_efficiency](docs/05_token_efficiency.md) | недетерминированный (behavioral) | токены/латентность vs прирост coverage | не реализован |
| 06 | [redteam](docs/06_redteam.md) | недетерминированный (adversarial) | устойчивость к prompt injection / расширению прав | не реализован |

Гейты 01-02 дешёвые и детерминированные — гонять на каждый push.
Гейты 03-06 дороже — см. `docs/` по каждому гейту про рекомендованную частоту.

## Структура

```
gates/       — код гейтов (по одному модулю на гейт)
docs/        — контракт каждого гейта: вход, логика, порог, выход
policies/    — allowlist прав, критерии рубрики
examples/    — скиллы-фикстуры, каждый рассчитан на провал конкретного гейта
config.yaml  — порядок гейтов, пороги, пути
run_gates.py — последовательный раннер (fail-fast)
```

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка судьи (гейт 04, в дальнейшем 05-06)

Нужен ключ Anthropic API — креды в репозитории не хранятся, только env:

```bash
export JUDGE_API_KEY=sk-ant-...        # или ANTHROPIC_API_KEY
export JUDGE_BASE_URL=...              # опционально: свой прокси вместо api.anthropic.com
export JUDGE_MODEL=claude-sonnet-5     # опционально, дефолт см. config.yaml -> judge.model
```

Без ключа гейт 04 возвращает `not_configured` (не блокирует цепочку, но и не
пропускает молча — статус явно виден в отчёте).

## Запуск

```bash
python3 run_gates.py                       # прогнать все скиллы из examples/skills
python3 run_gates.py --skill golden-skill  # один скилл
```

## Ожидаемые результаты на фикстурах

См. `examples/expected_results.yaml` — какой гейт должен упасть на каком
примере. Это оракул для проверки самих гейтов, когда они реализуются.
