"""Гейт 02 — права/MCP-scope. Детерминированный. См. docs/02_permissions.md.

evaluate_tools() вынесен как переиспользуемый движок политики — гейт 07
(docs/roadmap_chains.md) прогоняет через него не только собственные tools:
скилла, а объединение по транзитивному замыканию uses: (эскалация прав
через композицию).

_load_policy() валидирует саму политику и кидает PolicyError с понятным
сообщением: политику правят владельцы систем разной квалификации
(см. docs/personas.md, портрет 3), и её поломка — их ошибка, а не ошибка
автора скилла; без валидации битая политика либо роняла раннер с
traceback'ом, либо молча превращалась в «инструмента нет в allowlist» —
и гейт советовал автору скилла добавить инструмент, который владелец
только что добавил."""
import difflib
import fnmatch
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL
from gates.g01_static import _read_skill

POLICY_PATH = Path(__file__).parent.parent / "policies" / "tools_allowlist.yaml"
POLICY_REL = "policies/tools_allowlist.yaml"
# Политики систем (решение 1C, см. docs/design_multisystem_groups.md):
# владелец системы держит СВОЙ файл policies/systems/<system>.yaml и не может
# задеть чужой. Файлы <корень политики>/systems/*.yaml мёрджатся с глобальной
# политикой при загрузке (_load_policy).

# Все три ключа обязательны: молча исчезнувший always_flag_for_review — это
# молча снятый предохранитель реестра. Сознательно пустой список пишется
# явно: 'always_flag_for_review: []'.
_POLICY_KEYS = ("allowed_by_default", "requires_justification", "always_flag_for_review")


class PolicyError(Exception):
    """Сама политика сломана (не парсится / не та форма / противоречива)."""


def _rel(path: Path) -> str:
    """Путь в сообщениях об ошибках — относительно корня репозитория, где
    возможно (кликабельно и коротко), иначе как есть (фикстуры/внешние)."""
    try:
        return str(path.relative_to(Path(__file__).parent.parent))
    except ValueError:
        return str(path)


def _line_of(raw: str, needle: str) -> str:
    """Best-effort номер строки записи в файле политики — тот же приём, что
    локации в сообщениях гейта 01 (прогон роли «новичок»)."""
    for i, line in enumerate(raw.splitlines(), 1):
        if needle in line:
            return f" (строка {i})"
    return ""


def _validate_policy(policy, raw: str) -> list:
    if not isinstance(policy, dict):
        return [
            "ожидается YAML-маппинг с ключами "
            + ", ".join(_POLICY_KEYS)
            + f", а распарсился {type(policy).__name__}"
        ]

    errors = []

    for key in policy:
        if key not in _POLICY_KEYS:
            hint = difflib.get_close_matches(key, _POLICY_KEYS, n=1, cutoff=0.6)
            suffix = f" — возможно, опечатка в '{hint[0]}'" if hint else ""
            errors.append(f"неизвестный ключ '{key}'{suffix}{_line_of(raw, key)}")

    for key in _POLICY_KEYS:
        if key not in policy:
            errors.append(
                f"нет обязательного ключа '{key}' — если список сознательно "
                f"пуст, напиши '{key}: []' явно"
            )
            continue
        value = policy[key]
        if value is None:
            continue  # 'key:' без элементов — YAML даёт None, считаем пустым списком
        if isinstance(value, str):
            errors.append(
                f"'{key}' должен быть списком ('- элемент' на строку), а не "
                f"строкой — CSV-запись здесь не работает (в отличие от tools: "
                f"в frontmatter скилла){_line_of(raw, key)}"
            )
            continue
        if not isinstance(value, list):
            errors.append(f"'{key}' должен быть списком, а не {type(value).__name__}")
            continue
        for item in value:
            if not isinstance(item, str):
                errors.append(f"в '{key}' не-строковая запись: {item!r}")
            elif any(ch.isspace() for ch in item):
                errors.append(
                    f"в '{key}' запись с пробелом: '{item}' — так не выглядит "
                    f"ни один инструмент; похоже, две строки списка склеились "
                    f"из-за сбитого отступа{_line_of(raw, item.split()[0])}"
                )
    if errors:
        return errors

    # Форма валидна — проверяем противоречия между списками.
    allowed = [t for t in (policy.get("allowed_by_default") or []) if isinstance(t, str)]
    needs_just = [t for t in (policy.get("requires_justification") or []) if isinstance(t, str)]
    always_flag = [t for t in (policy.get("always_flag_for_review") or []) if isinstance(t, str)]

    for tool in allowed:
        if tool in needs_just:
            errors.append(
                f"'{tool}' одновременно в allowed_by_default и в "
                f"requires_justification — непонятно, нужно ли обоснование; "
                f"оставь в одном списке{_line_of(raw, tool)}"
            )

    # Мёртвые записи: предохранитель always_flag_for_review срабатывает
    # раньше обоих списков, поэтому такая запись только обещает то, чего
    # гейт никогда не сделает.
    for key, tools in (("allowed_by_default", allowed), ("requires_justification", needs_just)):
        for tool in tools:
            for pattern in always_flag:
                if fnmatch.fnmatch(tool, pattern):
                    errors.append(
                        f"'{tool}' в {key} подпадает под предохранитель "
                        f"'{pattern}' (always_flag_for_review) и всё равно "
                        f"уйдёт на ручное ревью — запись мёртвая; убери её "
                        f"или согласуй изменение предохранителя с админом "
                        f"реестра{_line_of(raw, tool)}"
                    )

    return errors


# Форма файла политики системы (policies/systems/<system>.yaml).
# always_flag_for_review сюда сознательно не входит: предохранитель —
# глобальный, владелец системы не может его ни расширить, ни снять.
_SYSTEM_KEYS = ("system", "namespace", "allowed_by_default", "requires_justification")


def _validate_system_policy(data, raw: str, stem: str) -> list:
    """«Гейт для политик» (следствие 2 модели владения в design-note):
    владелец разной квалификации получает newbie-grade ошибки и не может
    выйти за свой namespace или тронуть предохранитель."""
    if not isinstance(data, dict):
        return [f"ожидается YAML-маппинг с ключами {', '.join(_SYSTEM_KEYS)}, "
                f"а распарсился {type(data).__name__}"]

    errors = []
    for key in data:
        if key in _SYSTEM_KEYS:
            continue
        if key == "always_flag_for_review":
            errors.append(
                "'always_flag_for_review' в файле системы запрещён — "
                "предохранитель глобальный (policies/tools_allowlist.yaml), "
                "владелец системы его не меняет; согласуй с админом реестра"
            )
            continue
        hint = difflib.get_close_matches(key, _SYSTEM_KEYS, n=1, cutoff=0.6)
        suffix = f" — возможно, опечатка в '{hint[0]}'" if hint else ""
        errors.append(f"неизвестный ключ '{key}'{suffix}{_line_of(raw, key)}")

    system = data.get("system")
    if not isinstance(system, str) or not system:
        errors.append("нет обязательного ключа 'system' (имя системы строкой)")
    elif system != stem:
        errors.append(
            f"system: '{system}' не совпадает с именем файла '{stem}.yaml' — "
            f"файл и система должны называться одинаково"
        )

    namespace = data.get("namespace")
    ns_prefix = None
    if not isinstance(namespace, str) or not namespace:
        errors.append(
            "нет обязательного ключа 'namespace' — паттерн инструментов "
            "системы вида mcp__<system>__*"
        )
    elif not (namespace.startswith("mcp__") and namespace.endswith("*")
              and "*" not in namespace[:-1]):
        errors.append(
            f"namespace '{namespace}' должен иметь вид mcp__<system>__* "
            f"(начинаться с mcp__, заканчиваться одной *)"
        )
    else:
        ns_prefix = namespace[:-1]

    for key in ("allowed_by_default", "requires_justification"):
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"'{key}' должен быть списком ('- элемент' на строку)")
            continue
        for item in value:
            if not isinstance(item, str):
                errors.append(f"в '{key}' не-строковая запись: {item!r}")
            elif any(ch.isspace() for ch in item):
                errors.append(
                    f"в '{key}' запись с пробелом: '{item}' — похоже, две "
                    f"строки списка склеились из-за сбитого отступа"
                    f"{_line_of(raw, item.split()[0])}"
                )
            elif ns_prefix is not None and not item.startswith(ns_prefix):
                errors.append(
                    f"'{item}' в {key} выходит за namespace '{namespace}' — "
                    f"владелец системы управляет только своими инструментами"
                    f"{_line_of(raw, item)}"
                )
    return errors


def _load_system_policies(systems_dir: Path) -> tuple:
    """Возвращает (allowed_extra, requires_extra) из <systems_dir>/*.yaml.
    Записи могут быть fnmatch-паттернами (решение 1A внутри 1C) — но
    предохранитель always_flag_for_review на рантайме побеждает любой
    паттерн, так что слишком широкий паттерн не открывает send/delete,
    а лишь не делает того, на что владелец надеялся."""
    if not systems_dir.is_dir():
        return [], []
    allowed_extra, requires_extra = [], []
    prefixes = {}
    all_errors = []
    for path in sorted(systems_dir.glob("*.yaml")):
        rel = _rel(path)
        raw = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            loc = f" (строка {mark.line + 1})" if mark is not None else ""
            problem = getattr(exc, "problem", None) or str(exc)
            all_errors.append(f"{rel}: не парсится как YAML{loc}: {problem}")
            continue
        errors = _validate_system_policy(data, raw, path.stem)
        if errors:
            all_errors.extend(f"{rel}: {e}" for e in errors)
            continue
        ns_prefix = data["namespace"][:-1]
        for other_rel, other_prefix in prefixes.items():
            if ns_prefix.startswith(other_prefix) or other_prefix.startswith(ns_prefix):
                all_errors.append(
                    f"{rel}: namespace '{data['namespace']}' пересекается с "
                    f"namespace файла {other_rel} — одна система, один владелец"
                )
        prefixes[rel] = ns_prefix
        allowed_extra.extend(data.get("allowed_by_default") or [])
        requires_extra.extend(data.get("requires_justification") or [])
    if all_errors:
        raise PolicyError(
            "политика систем сломана — это проблема файла политики, не "
            "скилла; чинить названный файл: " + "; ".join(all_errors)
        )
    return allowed_extra, requires_extra


def _load_policy(policy_path: Path = None):
    """Читает и валидирует политику (глобальную + файлы систем из <root>/systems,
    решение 1C). policy_path по умолчанию — живая POLICY_PATH; фикстуры битых
    политик (examples/policies/*) подставляют свой tools_allowlist.yaml.
    PolicyError — проблема политики (зона ответственности владельца
    системы/админа), не скилла."""
    if policy_path is None:
        policy_path = POLICY_PATH
    rel = _rel(policy_path)
    raw = policy_path.read_text(encoding="utf-8")
    try:
        policy = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        loc = ""
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            loc = f" (строка {mark.line + 1}, колонка {mark.column + 1})"
        problem = getattr(exc, "problem", None) or str(exc)
        raise PolicyError(
            f"политика {rel} не парсится как YAML{loc}: {problem} — "
            f"это проблема политики, не скилла; чинить файл политики"
        )
    errors = _validate_policy(policy, raw)
    if errors:
        raise PolicyError(
            f"политика {rel} сломана — это проблема политики, не "
            f"скилла; чинить файл политики: " + "; ".join(errors)
        )
    allowed_extra, requires_extra = _load_system_policies(policy_path.parent / "systems")
    if allowed_extra or requires_extra:
        # Мёртвые записи из файлов систем: литерал, подпадающий под глобальный
        # предохранитель — та же проверка, что в _validate_policy для
        # глобального файла, но выполнимая только после мёрджа (у файла
        # системы нет своего always_flag). Паттерн-записи не сверяются —
        # формальное пересечение глобов даёт ложные срабатывания, см.
        # docs/02_permissions.md; для них авторитетен рантайм-предохранитель.
        always_flag = policy.get("always_flag_for_review") or []
        dead_errors = []
        for key, extra in (
            ("allowed_by_default", allowed_extra),
            ("requires_justification", requires_extra),
        ):
            for tool in extra:
                if _is_pattern(tool):
                    continue
                for pattern in always_flag:
                    if fnmatch.fnmatch(tool, pattern):
                        dead_errors.append(
                            f"'{tool}' ({key} в файле своей системы) подпадает "
                            f"под глобальный предохранитель '{pattern}' и всё "
                            f"равно уйдёт на ручное ревью — запись мёртвая; "
                            f"убери её или согласуй с админом реестра"
                        )
        if dead_errors:
            raise PolicyError(
                "политика систем сломана — это проблема файла политики, не "
                "скилла: " + "; ".join(dead_errors)
            )
        policy = dict(policy)
        policy["allowed_by_default"] = list(policy.get("allowed_by_default") or []) + allowed_extra
        policy["requires_justification"] = (
            list(policy.get("requires_justification") or []) + requires_extra
        )
    return policy


def _is_pattern(entry: str) -> bool:
    return any(ch in entry for ch in "*?[")


def _justification_for(tool: str, base_tool: str, justification) -> str:
    """Обоснование для конкретного инструмента. Строка — blanket на весь
    скилл (путь новичка, поведение не менялось). Карта {tool_or_pattern:
    reason} — per-tool гранулярность (решение 3B): сначала точный ключ,
    потом паттерн-ключ. Нет записи — обоснования нет."""
    if isinstance(justification, str):
        return justification.strip()
    if isinstance(justification, dict):
        for key in (tool, base_tool):
            reason = justification.get(key)
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
        for key, reason in justification.items():
            if not (isinstance(key, str) and isinstance(reason, str) and reason.strip()):
                continue
            if _is_pattern(key) and (fnmatch.fnmatch(tool, key) or fnmatch.fnmatch(base_tool, key)):
                return reason.strip()
    return ""


def evaluate_tools(tools: list, justification, policy: dict) -> tuple:
    """Прогоняет список инструментов через allowlist/requires_justification.
    justification — строка (blanket), карта {tool_or_pattern: reason} или
    None. Записи политики — точные имена или fnmatch-паттерны (паттерны
    приходят из policies/systems/*.yaml; в allowed сопоставляется полное имя
    инструмента, в requires — базовое имя без скобок, как и раньше).
    Не формирует сообщение по always_flag_for_review — разным вызывающим
    (гейт 02 про собственные tools:, гейт 07 про унаследованные через
    uses:) нужна разная формулировка для одного и того же списка flagged.
    Возвращает (errors: list[str], flagged: list[(tool, pattern)])."""
    allowed = policy.get("allowed_by_default") or []
    needs_just = policy.get("requires_justification") or []
    always_flag = policy.get("always_flag_for_review") or []

    errors = []
    flagged = []

    if justification is not None and not isinstance(justification, (str, dict)):
        errors.append(
            f"justification должен быть строкой или картой "
            f"{{инструмент: причина}}, а не {type(justification).__name__}"
        )
        justification = None
    is_map = isinstance(justification, dict)

    for tool in tools:
        for pattern in always_flag:
            if fnmatch.fnmatch(tool, pattern):
                flagged.append((tool, pattern))

        base_tool = tool.split("(")[0]
        if any(e == tool or (_is_pattern(e) and fnmatch.fnmatch(tool, e)) for e in allowed):
            continue
        if any(
            e == base_tool
            or (_is_pattern(e) and (fnmatch.fnmatch(tool, e) or fnmatch.fnmatch(base_tool, e)))
            for e in needs_just
        ):
            if not _justification_for(tool, base_tool, justification):
                where = (
                    "не имеет записи в карте justification (или запись пустая) — "
                    "добавь для него причину"
                    if is_map
                    else "требует поля 'justification' в frontmatter"
                )
                errors.append(f"инструмент '{tool}' {where}")
        else:
            errors.append(
                f"инструмент '{tool}' не в allowlist и не в списке requires_justification — "
                f"поле justification на него не действует, пока он не внесён в политику. "
                f"Добавь '{tool}' в policies/tools_allowlist.yaml (или в политику своей "
                f"системы policies/systems/<system>.yaml): в requires_justification, если "
                f"он должен пропускаться с обоснованием, или в allowed_by_default, если "
                f"безопасен по умолчанию"
            )

    return errors, flagged


def check(skill_path: Path) -> GateResult:
    frontmatter, _, text = _read_skill(skill_path)
    if frontmatter is None:
        return GateResult(FAIL, "нет frontmatter — гейт 01 должен был отсеять раньше", {})

    try:
        policy = _load_policy()
    except PolicyError as exc:
        return GateResult(FAIL, str(exc), {"policy_error": True})
    tools = frontmatter.get("tools", []) or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",")]
    # Сырое значение: строка ИЛИ карта {tool: reason} (решение 3B) —
    # нормализует evaluate_tools, а не вызывающий.
    justification = frontmatter.get("justification")

    errors, flagged = evaluate_tools(tools, justification, policy)

    if flagged:
        details_str = ", ".join(f"{t} ~ {p}" for t, p in flagged)
        errors.append(
            f"требует ручного review человеком вне этого пайплайна (always_flag_for_review, "
            f"не проходит автоматически даже с обоснованием — гейт 06 для этого PR не запустится, "
            f"цепочка остановлена раньше): {details_str}"
        )

    if errors:
        return GateResult(FAIL, "; ".join(errors), {"errors": errors, "flagged": flagged})
    return GateResult(PASS, "права скилла соответствуют политике", {})
