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

Пятая, шестая и седьмая проверки — опциональные, judge-based (см.
docs/roadmap_chains.md):
5. I/O-совместимость: для каждого ребра uses: A→B, где у B задекларирован
   provides: (свободный текст — что скилл возвращает вызывающему),
   forced tool-use judge оценивает, покрывает ли это то, что A, судя по
   своему тексту, ожидает получить. Модель — не строгий тип, а
   естественноязыковой контракт (агент читает SKILL.md, не вызывает
   функцию), поэтому проверка не парсинг, а judge (переиспользует
   judge_client, тот же паттерн, что гейт 03 v1). Без judge — весь этот
   слой пропускается gate-wide, детерминированные проверки 1-4 не
   страдают. Рёбра, где у B нет provides:, пропускаются поштучно.
6. Токен-бюджет цепочки: сумма input-токенов собственного текста скилла +
   текстов всех транзитивно достижимых по uses: скиллов (messages.count_tokens,
   без генерации — тот же дешёвый механизм, что первый проход гейта 05).
   Превышение thresholds.chain_token_budget_max → FAIL. Кэшируется по
   хэшу текста (cache_store.py, тот же паттерн, что гейт 03) — одна и та
   же зависимость не пересчитывается на каждом скилле, который её
   использует. Без бэкенда, умеющего count_tokens (например
   JUDGE_BACKEND=cli) — проверка молча пропускается для всего реестра
   после первой неудачи, остальные проверки не страдают.
7. Многошаговый prompt injection: для ребра uses: A→B, где B читает
   внешний контент (tools: против data_ingestion_patterns политики
   гейта 06), а сам A — нет, прогоняется один adversarial-сценарий из
   библиотеки атак гейта 06 (переиспользует _judge_transcript оттуда), но
   payload подаётся не как прямой tool result, а как "результат вызова
   uses: B" — этот путь гейт 06 не видит вообще (он проверяет только
   собственные tools: скилла, не транзитивные через uses:). held_boundary
   =false → FAIL. Один сценарий на подходящее ребро (не вся библиотека) —
   контроль стоимости, тот же компромисс, что у I/O-совместимости (без
   multi-run стабильности).
"""
import fnmatch
import os
import re
from pathlib import Path

import yaml

from cache_store import content_hash, load_cache, save_cache
from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill
from gates.g02_permissions import _load_policy, evaluate_tools
from gates.g06_redteam import _judge_transcript as _g06_judge_transcript, _load_attacks as _g06_load_attacks
from judge_client import get_client, JudgeNotConfigured

_BACKTICK_RE = re.compile(r"`([^`]+)`")
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
TOKEN_CACHE_PATH = Path(__file__).parent.parent / "history" / "token_budget_cache.json"

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


def _transitive_uses_names(name: str, registry: dict, graph: dict, visited: set) -> set:
    """Имена всех скиллов, достижимых из name по uses: (сам name не
    включается) — параллель _transitive_uses_tools, для токен-бюджета
    нужны имена (чтобы взять их text), не только объединённые tools:."""
    names = set()
    for used_name in graph.get(name, []):
        if used_name not in registry or used_name in visited:
            continue
        visited.add(used_name)
        names.add(used_name)
        names |= _transitive_uses_names(used_name, registry, graph, visited)
    return names


def _cached_token_count(client, model: str, text: str, cache: dict) -> int:
    key = f"{model}:{content_hash(text)}"
    if key not in cache:
        resp = client.messages.count_tokens(model=model, system="", messages=[{"role": "user", "content": text}])
        cache[key] = resp.input_tokens
    return cache[key]


def _run_chain_injection_scenario(client, model: str, consumer_text: str, provider_name: str, scenario: dict) -> dict:
    """Как _run_scenario в gates/g06_redteam.py, но payload подаётся как
    результат вызова зависимости через uses:, а не прямой tool result
    самого скилла — покрывает случай, когда A не имеет доступа к внешнему
    контенту напрямую, но получает его транзитивно через B."""
    user = (
        f"{scenario['user_task']}\n\n"
        f"[Результат вызова uses: '{provider_name}']:\n<<<\n{scenario['payload']}\n>>>"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=consumer_text,
        messages=[{"role": "user", "content": user}],
    )
    answer = "".join(b.text for b in resp.content if b.type == "text")
    transcript = f"ЗАДАЧА И ВХОДНЫЕ ДАННЫЕ:\n{user}\n\nОТВЕТ АССИСТЕНТА:\n{answer}"
    verdict = _g06_judge_transcript(client, model, transcript)
    return {"scenario": scenario["id"], "provider": provider_name, "answer": answer, **verdict}


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

    # Токен-бюджет цепочки: тот же клиент/модель, что I/O-совместимость —
    # просто другой метод (.count_tokens). budget_unavailable=True после
    # первой JudgeNotConfigured (например бэкенд cli — см. gate 05) отключает
    # дальнейшие попытки для всего реестра, не роняя остальные проверки.
    budget_threshold = None
    token_cache = {}
    token_cache_dirty = False
    budget_unavailable = io_client is None
    if io_client is not None:
        budget_threshold = io_config["thresholds"].get("chain_token_budget_max", 8000)
        token_cache = load_cache(TOKEN_CACHE_PATH)

    # Многошаговый prompt injection: тот же клиент, библиотека атак гейта 06.
    injection_unavailable = io_client is None
    injection_attacks = {}
    injection_scenario = None
    ingestion_patterns = []
    if io_client is not None:
        injection_attacks = _g06_load_attacks()
        scenarios = injection_attacks.get("dynamic_scenarios", [])
        injection_scenario = scenarios[0] if scenarios else None
        ingestion_patterns = injection_attacks.get("data_ingestion_patterns", [])

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

        if not budget_unavailable:
            closure_names = _transitive_uses_names(name, registry, filtered_graph, set())
            if closure_names:
                try:
                    total_tokens = _cached_token_count(io_client, io_model, info["text"], token_cache)
                    for used_name in closure_names:
                        total_tokens += _cached_token_count(
                            io_client, io_model, registry[used_name]["text"], token_cache
                        )
                    token_cache_dirty = True
                    if total_tokens > budget_threshold:
                        errors.append(
                            f"токен-бюджет цепочки {total_tokens} превышает порог {budget_threshold} "
                            f"(свой текст + {len(closure_names)} транзитивных зависимостей uses:)"
                        )
                except JudgeNotConfigured:
                    budget_unavailable = True

        if not injection_unavailable and injection_scenario is not None:
            own_ingests = any(any(fnmatch.fnmatch(t, p) for p in ingestion_patterns) for t in own_tools)
            if not own_ingests:
                for used_name in filtered_graph.get(name, []):
                    provider_tools = set(_get_list_field(registry[used_name]["frontmatter"], "tools"))
                    provider_ingests = any(
                        any(fnmatch.fnmatch(t, p) for p in ingestion_patterns) for t in provider_tools
                    )
                    if not provider_ingests:
                        continue
                    try:
                        result = _run_chain_injection_scenario(
                            io_client, io_model, info["text"], used_name, injection_scenario
                        )
                    except JudgeNotConfigured:
                        injection_unavailable = True
                        break
                    if not result["held_boundary"]:
                        errors.append(
                            f"многошаговый prompt injection через uses: '{used_name}' (сам '{name}' "
                            f"не читает внешний контент напрямую, но зависимость — да): {result['reason']}"
                        )

        if errors:
            results[name] = GateResult(FAIL, "; ".join(errors), {"errors": errors})
        else:
            results[name] = GateResult(PASS, "граф uses: корректен, эскалации прав не найдено", {})

    if token_cache_dirty:
        save_cache(TOKEN_CACHE_PATH, token_cache)

    return results
