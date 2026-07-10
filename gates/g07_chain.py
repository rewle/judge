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

Отложено за пределы этой версии (см. docs/roadmap_chains.md): совместимость
input/output между скиллами, совокупный токен-бюджет цепочки.
"""
import re
from pathlib import Path

from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill
from gates.g02_permissions import _load_policy, evaluate_tools

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _get_list_field(frontmatter: dict, key: str) -> list:
    value = frontmatter.get(key, []) or []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    return value


def _load_registry(registry_dir: Path) -> dict:
    """name -> {"dir": Path, "frontmatter": dict, "body": str}. Скиллы без
    валидного frontmatter молча пропускаются — их должен был отсеять
    гейт 01 раньше (см. докстринг модуля: гейт 07 рассчитан на прогон
    после 01-06)."""
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
        registry[name] = {"dir": skill_dir, "frontmatter": frontmatter, "body": body or ""}
    return registry


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

        if errors:
            results[name] = GateResult(FAIL, "; ".join(errors), {"errors": errors})
        else:
            results[name] = GateResult(PASS, "граф uses: корректен, эскалации прав не найдено", {})

    return results
