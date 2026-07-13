# TODO: прогон и калибровка через api-бэкенд

Следующий (и единственный оставшийся) этап проекта. Блокер — живой платный
ключ Anthropic API (`JUDGE_API_KEY`/`ANTHROPIC_API_KEY`), см. AGENTS.md
«Текущий статус». Логика гейтов 04/06 подтверждена через `JUDGE_BACKEND=cli`,
но пороги в `config.yaml` не откалиброваны — их нельзя крутить «на глазок»,
только по данным api-прогона.

## 1. Подготовка

```bash
export JUDGE_API_KEY=sk-ant-...   # или ANTHROPIC_API_KEY; api — бэкенд по умолчанию
```

Модель и число прогонов уже в `config.yaml` (`claude-sonnet-5`, `n_runs: 8`).
Смоук на одном скилле — проверить ключ и увидеть цену одного прогона:

```bash
python3 run_gates.py --skill golden-skill
```

## 2. Полный прогон на фикстурах

- `python3 run_gates.py` — все скилл-фикстуры `examples/skills` через
  LLM-гейты 03(v1 semantic)/04/05/06(adversarial), затем 07 автоматически;
- `python3 run_gates.py --check-chains --registry examples/chains` —
  chain-фикстуры (I/O-совместимость и токен-бюджет гейта 07);
- группы `examples/groups/*` через `--skills-dir`;
- сверка с оракулами `examples/expected_results.yaml` и
  `examples/policies/expected_policy_results.yaml` — всё это разом покрывает
  `/judge-regress`.

Контроль стоимости: гейт 04 — `n_runs` вызовов на скилл; гейт 03 v1 —
O(N^2) по парам, но пред-фильтр `duplication_semantic_prefilter` срезает
большинство. Кэши в `history/` делают повторные прогоны почти бесплатными,
а пороги применяются ПОСЛЕ кэша — калибровка не требует пережигать вызовы.

Статус: пункт проведён через `JUDGE_BACKEND=cli` (без платного ключа);
через `api` — предстоит.

## 3. Калибровка порогов

По фактическим распределениям баллов на хороших vs плохих фикстурах
откалибровать 8 порогов:

- `rubric_min_score`
- `rubric_max_criterion_sigma`
- `duplication_semantic_similarity`
- `token_efficiency_max_overhead_pct`
- `token_efficiency_min_coverage_gain_pp`
- `rubric_drift_warn_abs`
- `chain_io_compat_min_score`
- `chain_token_budget_max`

Метод: смотреть, где ложатся golden- и плохие фикстуры, ставить порог с
зазором, фиксировать rationale в комментариях `config.yaml`.

## 4. Финал

Чистая регрессия по всем оракулам через `api`, коммит калибровки. После
этого по AGENTS.md — интеграция с реальным реестром скиллов / CI.
