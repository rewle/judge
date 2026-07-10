"""Гейт 07 — граф зависимостей skill->skill (uses:). См. docs/roadmap_chains.md.

В отличие от гейтов 01-06 (check(skill_path) на одном скилле), этот гейт
реестровый: check_registry(registry_dir) видит весь граф разом — цикл или
эскалация прав через композицию не определяются по одному скиллу в
изоляции. Вызывается отдельно, не из последовательной fail-fast цепочки
run_skill() в run_gates.py (см. --check-chains).

Конвенция: skill.frontmatter.uses — плоский список имён других скиллов
реестра (matching их frontmatter.name), декларация, не инференс из тела —
симметрично tools: в гейте 02.

Четыре детерминированные проверки, без judge:
1. Существование каждой ссылки uses: в реестре.
2. Ацикличность графа (топологически, через DFS с цветами).
3. Эскалация прав через композицию: объединение tools: по транзитивному
   замыканию uses:, прогон через ту же политику allowlist, что гейт 02
   (evaluate_tools) — ловит "A с виду безобидный, но через uses: [B]
   эффективно получает Bash(*) от B".
4. Обратная проверка: тело скилла в backtick-споте (`` `skill-name` ``)
   упоминает имя другого скилла реестра, не задекларированного в uses: —
   необъявленная зависимость, гейт 02 её не увидит вообще (не знает, что
   искать). Эвристика намеренно узкая (точное совпадение имени внутри
   backtick-спана вне fenced-блоков) — минимизирует ложные срабатывания
   ценой пропуска вызовов, упомянутых без backtick-оформления.

Пятая проверка — опциональная, judge-based (см. docs/roadmap_chains.md,
"I/O-совместимость"):
5. I/O-совместимость: для каждого ребра uses: A→B, где у B задекларирован
   provides: (свободный текст — что скилл возвращает вызывающему),
   forced tool-use judge оценивает, покрывает ли это то, что A, судя по
   своему тексту, ожидает получить. Модель — не строгий тип, а
   естественноязыковой контракт (агент читает SKILL.md, не вызывает
   функцию), поэтому проверка не парсинг, а judge (переиспользует
   judge_client, тот же паттерн, что гейт 03 v1). Без judge — весь этот
   слой пропускается gate-wide, детерминированные проверки 1-4 не
   страдают. Рёбра, где у B нет provides:, пропускаются поштучно.

Совокупный токен-бюджет цепочки и многошаговый prompt injection —
по-прежнему отложены (см. docs/roadmap_chains.md).
"""
import os
import re
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill
from gates.g02_permissions import _load_policy, evaluate_tools
from judge_client import get_client, JudgeNotConfigured

_BACKTICK_RE = re.compile(r"`([^`]+)`")
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

_IO_COMPAT_TOOL = {
    "name": "submit_io_compat_verdict",
    "description": (
        "Оценить, покрывает ли заявленный provides: скилла B то, что скилл A, "
        "судя по его собственному тексту, ожидает получить, вызывая B через uses:."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "compatible": {"type": "boolean"},
            "score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0 — совсем не то, что нужно A; 1 — полностью покрывает ожидание",
            },
            "reason": {"type": "string"},
        },
        "required": ["compatible", "score", "reason"],
    },
}

IO_JUDGE_SYS = (
    "Ты проверяешь совместимость на стыке двух скиллов в реестре. Скилл A вызывает "
    "скилл B (uses:) как часть своей работы. Оцени: то, что B заявляет как результат "
    "(provides:), покрывает ли то, что A, судя по его собственному тексту, ожидает "
    "получить, вызывая B. Это не типизированный контракт — не придирайся к формату "
    "или структуре, если смысл по существу сходится."
)


def _get_list_field(frontmatter: dict, key: str) -> list:
    value = frontmatter.get(key, []) or []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    return value


def _load_registry(registry_dir: Path) -> dict:
    """name -> {"dir": Path, "frontmatter": dict, "body": str, "text": str}.
    Скиллы без валидного frontmatter молча пропускаются — их должен был
    отсеять гейт 01 раньше (см. докстринг модуля: гейт 07 рассчитан на
    прогон после 01-06)."""
    registry = {}
    for skill_dir in sorted(registry_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        frontmatter, body, text = _read_skill(skill_dir)
        if frontmatter is None:
            continue
        name = frontmatter.get("name")
        if not name:
            continue
        registry[name] = {"dir": skill_dir, "frontmatter": frontmatter, "body": body or "", "text": text or ""}
    return registry


def _judge_io_compat(client, model: str, consumer_text: str, provider_name: str, provider_fm: dict) -> dict:
    provider_desc = provider_fm.get("description", "") or ""
    provider_provides = provider_fm.get("provides", "") or ""
    user = (
        f"СКИЛЛ A (вызывающий, полный текст):\n<<<\n{consumer_text}\n>>>\n\n"
        f"СКИЛЛ B (вызывается через uses: '{provider_name}'):\n"
        f"description: {provider_desc}\n"
        f"provides: {provider_provides}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        system=IO_JUDGE_SYS,
        messages=[{"role": "user", "content": user}],
        tools=[_IO_COMPAT_TOOL],
        tool_choice={"type": "tool", "name": "submit_io_compat_verdict"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("judge не вернул tool_use блок с вердиктом о совместимости")


def _mentioned_skill_names(body: str, registry_names: set, self_name: str) -> set:
    """Имена скиллов реестра, упомянутые в теле в backtick-спане вне
    fenced-блоков. Не различает "инструктирует вызвать" от "упоминает по
    другой причине" (например в комментарии "не используй `X`") — это
    известное ограничение эвристики, см. докстринг модуля."""
    prose = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    mentioned = set()
    for match in _BACKTICK_RE.finditer(prose):
        token = match.group(1).strip()
        if token in registry_names and token != self_name:
            mentioned.add(token)
    return mentioned


def _find_cycle(graph: dict) -> list:
    """DFS с раскраской (белый/серый/чёрный). graph уже отфильтрован от
    несуществующих таргетов — dangling-ссылки не участвуют в поиске цикла,
    они репортятся отдельной проверкой. Возвращает путь первого найденного
    цикла (список имён, замкнутый — первое и последнее имя совпадают) или
    None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    parent = {}

    def dfs(u):
        color[u] = GRAY
        for v in graph.get(u, []):
            if color.get(v, WHITE) == WHITE:
                parent[v] = u
                found = dfs(v)
                if found:
                    return found
            elif color.get(v) == GRAY:
                path = [v]
                cur = u
                while cur != v:
                    path.append(cur)
                    cur = parent[cur]
                path.append(v)
                path.reverse()
                return path
        color[u] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            cycle = dfs(node)
            if cycle:
                return cycle
    return None


def _transitive_uses_tools(name: str, registry: dict, graph: dict, visited: set) -> set:
    """Инструменты всех скиллов, достижимых из name по uses: (сам name не
    включается). visited защищает от бесконечной рекурсии на циклах —
    цикл уже отдельно репортится _find_cycle, здесь достаточно не упасть."""
    tools = set()
    for used_name in graph.get(name, []):
        if used_name not in registry or used_name in visited:
            continue
        visited.add(used_name)
        tools |= set(_get_list_field(registry[used_name]["frontmatter"], "tools"))
        tools |= _transitive_uses_tools(used_name, registry, graph, visited)
    return tools


def check_registry(registry_dir: Path) -> dict:
    """Возвращает {skill_name: GateResult} для каждого скилла реестра с
    валидным frontmatter."""
    registry = _load_registry(registry_dir)
    registry_names = set(registry.keys())
    raw_graph = {name: _get_list_field(info["frontmatter"], "uses") for name, info in registry.items()}
    filtered_graph = {name: [u for u in uses if u in registry] for name, uses in raw_graph.items()}
    cycle = _find_cycle(filtered_graph)
    cycle_members = set(cycle[:-1]) if cycle else set()

    policy = _load_policy()

    io_client = None
    io_model = None
    io_threshold = None
    try:
        io_client = get_client()
    except JudgeNotConfigured:
        io_client = None
    if io_client is not None:
        io_config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        io_model = os.environ.get("JUDGE_MODEL", io_config.get("judge", {}).get("model", "claude-sonnet-5"))
        io_threshold = io_config["thresholds"].get("chain_io_compat_min_score", 0.6)

    results = {}
    for name, info in registry.items():
        errors = []

        missing = [u for u in raw_graph[name] if u not in registry]
        if missing:
            errors.append(f"uses: ссылается на несуществующий скилл: {', '.join(missing)}")

        if name in cycle_members:
            errors.append(f"цикл в графе uses: {' → '.join(cycle)}")

        own_tools = set(_get_list_field(info["frontmatter"], "tools"))
        closure_tools = _transitive_uses_tools(name, registry, filtered_graph, set())
        introduced = closure_tools - own_tools
        if introduced:
            tool_errors, flagged = evaluate_tools(sorted(introduced), justification="", policy=policy)
            if tool_errors:
                msg = (
                    f"эскалация прав через uses: инструменты {', '.join(sorted(introduced))} "
                    f"получены транзитивно, не задекларированы в tools: этого скилла напрямую — "
                    + "; ".join(tool_errors)
                )
                if flagged:
                    flagged_str = ", ".join(f"{t} ~ {p}" for t, p in flagged)
                    msg += f"; требует ручного ревью: {flagged_str}"
                errors.append(msg)

        mentioned = _mentioned_skill_names(info["body"], registry_names, name)
        undeclared = mentioned - set(raw_graph[name])
        if undeclared:
            errors.append(
                f"тело упоминает `{', '.join(sorted(undeclared))}` (backtick-спан), "
                f"но это не задекларировано в uses: — необъявленная зависимость"
            )

        if io_client is not None:
            for used_name in filtered_graph.get(name, []):
                provider_fm = registry[used_name]["frontmatter"]
                if not (provider_fm.get("provides") or "").strip():
                    continue
                verdict = _judge_io_compat(io_client, io_model, info["text"], used_name, provider_fm)
                if not verdict["compatible"] or verdict["score"] < io_threshold:
                    errors.append(
                        f"I/O-несовместимость с uses: '{used_name}' (score={verdict['score']:.2f}, "
                        f"порог {io_threshold}): {verdict['reason']}"
                    )

        if errors:
            results[name] = GateResult(FAIL, "; ".join(errors), {"errors": errors})
        else:
            results[name] = GateResult(PASS, "граф uses: корректен, эскалации прав не найдено", {})

    return results
