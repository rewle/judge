---
name: judge-regress
description: >
  Прогоняет run_gates.py на фикстурах examples/skills, сверяет фактический
  stop-gate с оракулом examples/expected_results.yaml, отдельно прогоняет гейт 07
  на examples/chains (для него оракула нет — показывается сырой результат).
  Использовать перед коммитом в gates/*.py или config.yaml (см. правило в
  AGENTS.md "перед коммитом... прогонять run_gates.py"), либо когда пользователь
  пишет "прогони регрессию", "/judge-regress", "проверь гейты".
  Не чинит гейты — только диагностика.
---

# Регрессия гейтов против оракула

Цель: заменить ручную сверку вывода `run_gates.py` с `examples/expected_results.yaml`
на автоматическую — без этого шага легко пропустить, что правка в `gates/*.py`
или `config.yaml` сдвинула stop-gate у существующей фикстуры (именно так это
делалось руками до появления этого скилла).

## Шаг 1. Прогнать, сверить с оракулом, показать только расхождения

```bash
cd /Users/rewle/Projects/judge && source .venv/bin/activate 2>/dev/null
python3 - <<'EOF'
import os
import re
import subprocess
import yaml
from pathlib import Path

ROOT = Path("/Users/rewle/Projects/judge")

# --- Доступность judge (эвристика, не полная копия judge_client.get_client()) ---
backend = os.environ.get("JUDGE_BACKEND", "api")
has_key = bool(os.environ.get("JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
judge_available = has_key or backend == "cli"
print(f"JUDGE_BACKEND={backend} | has_key={has_key} | judge_available={judge_available}")
print("(без judge гейты 04/05/06(adversarial)/03(v1)/07(judge-часть) легитимно вернут")
print(" NOT_CONFIGURED вместо FAIL — см. секцию «без стоп-гейта» ниже, это не всегда баг)")

# --- Шаг 1: per-skill регрессия против examples/expected_results.yaml ---
# --skip-chains: полный прогон без --skill/--path иначе триггерит гейт 07 на
# examples/skills автоматически (шум, не относящийся к этому оракулу).
proc = subprocess.run(
    ["python3", "run_gates.py", "--skip-chains"],
    cwd=ROOT, capture_output=True, text=True,
)
output = proc.stdout + proc.stderr

blocks = {}
current = None
for line in output.splitlines():
    m = re.match(r"^=== (.+?) ===$", line)
    if m:
        current = m.group(1)
        blocks[current] = []
        continue
    if current is not None:
        blocks[current].append(line)

def actual_stop_gate(lines):
    for line in lines:
        m = re.search(r"цепочка остановлена на (\S+)", line)
        if m:
            return m.group(1)
    return None  # все прошли/SKIP, явного FAIL нет

oracle_raw = yaml.safe_load((ROOT / "examples/expected_results.yaml").read_text(encoding="utf-8"))
oracle = {}
for key, val in oracle_raw.items():
    # оракул комбинирует пары фикстур одним ключом "a / b"
    for name in [n.strip() for n in key.split("/")]:
        oracle[name] = val

mismatches = []
no_stop = []
for name, expected in oracle.items():
    expected_gate = expected["expected_stop_gate"]
    if name not in blocks:
        mismatches.append((name, f"оракул ссылается на фикстуру, которой нет в выводе run_gates.py (ожидался стоп на {expected_gate})"))
        continue
    actual = actual_stop_gate(blocks[name])
    if actual == expected_gate:
        continue
    if actual is None:
        no_stop.append((name, expected_gate, expected.get("note", "").strip()))
    else:
        mismatches.append((name, f"неожиданный stop-gate: фактически {actual}, ожидалось {expected_gate}"))

print("\n=== Расхождения (реальная проблема — actual FAIL не там, где ожидал оракул) ===")
if mismatches:
    for name, reason in mismatches:
        print(f"{name}: {reason}")
else:
    print("нет")

print("\n=== Оракул ожидал стоп, но все гейты прошли/SKIP (проверить note — часто ожидаемо без judge) ===")
if no_stop:
    for name, expected_gate, note in no_stop:
        print(f"{name}: ожидался стоп на {expected_gate}")
        print(f"  note: {note}")
else:
    print("нет")

# --- Шаг 2: гейт 07 на examples/chains — оракула нет, только сырой результат ---
print("\n=== Гейт 07 на examples/chains (оракула нет, сырой результат) ===")
proc2 = subprocess.run(
    ["python3", "run_gates.py", "--check-chains", "--registry", "examples/chains"],
    cwd=ROOT, capture_output=True, text=True,
)
print((proc2.stdout + proc2.stderr).strip())
EOF
```

## Шаг 2. Сформировать отчёт

Секции в этом порядке:
- **Доступность judge** — одна строка (backend, есть ли ключ)
- **Расхождения** — если есть хоть одна строка, это приоритет №1: что-то в `gates/*.py`
  или `config.yaml` реально сдвинуло поведение против зафиксированного в оракуле —
  не коммитить, пока не разобрано
- **Оракул ожидал стоп, но всё прошло** — не диагноз сам по себе; свериться с note.
  Если note говорит "без judge — NOT_CONFIGURED, не FAIL" и judge сейчас недоступен —
  это ожидаемо. Если judge доступен (`judge_available=True`) и всё равно не остановилось —
  это уже расхождение, поднять как проблему
- **Гейт 07 на examples/chains** — просто показать вывод, оракула для сравнения нет

## Типичные ловушки

- Оракул (`examples/expected_results.yaml`) комбинирует пары фикстур одним ключом
  вида `"a / b"` — разбивать по `/`, иначе вторая фикстура пары не найдётся
- Полный прогон `run_gates.py` без `--skip-chains` триггерит гейт 07 на
  `examples/skills` (реестр из `config.yaml`) автоматически — это отдельная
  проверка графа `uses:` между фикстурами `examples/skills`, не имеет отношения
  к `expected_stop_gate` в оракуле; используем `--skip-chains`, чтобы не путать
  это с чистой per-skill регрессией
- `examples/chains` — отдельный набор фикстур специально под гейт 07, без
  зафиксированного machine-readable оракула (ожидания расписаны прозой в
  `docs/roadmap_chains.md`); не выдумывать оракул на лету — просто показать
  сырой результат
- `judge_available` в Шаге 1 — грубая эвристика (`JUDGE_API_KEY`/`ANTHROPIC_API_KEY`
  или `JUDGE_BACKEND=cli`), не повторяет все нюансы `judge_client.get_client()`
  (например `JUDGE_BASE_URL`) — годится только чтобы объяснить, почему часть
  гейтов `SKIP(CFG)`, не как точная замена реальной проверки конфигурации
- Не чинить фикстуры/гейты автоматически по результатам этого скилла — только
  показать; правки отдельным запросом
