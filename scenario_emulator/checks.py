"""Точные проверки трейса (Фаза 2) — без LLM. См.
docs/09_scenario_emulator.md, разделы "Формат сценария" (таблица типов
`exact_checks`) и "Формат отчёта". Список типов там прямо помечен как
предложение Фазы 0, не отдельно согласованное — реализован как есть,
корректировать по потребности.

Каждый check — dict вида {"type": ..., ...поля...} из frontmatter
сценария (scenario_emulator.scenario.Scenario.exact_checks). Результат —
{"type", "passed", "detail"} на каждый check, без побочных эффектов."""
import re

_ROLE_TO_EVENT_TYPE = {"user": "user_message", "assistant": "assistant_message"}


class CheckSpecError(ValueError):
    """exact_checks во frontmatter сценария — не то, что ожидает checker
    (опечатка в type, отсутствует обязательное поле). Отличается от
    provалившейся проверки (passed=False) — это ошибка формы сценария."""


def _get_path(value, path: str):
    """Точечный путь вида 'session.status' или 'items.0.name' — числовые
    сегменты индексируют список, остальные — ключ словаря. Бросает
    KeyError/IndexError/TypeError наверх, вызывающий код превращает их в
    passed=False с понятным detail."""
    for segment in path.split("."):
        if isinstance(value, list):
            value = value[int(segment)]
        else:
            value = value[segment]
    return value


def _check_message_contains(check: dict, events: list) -> dict:
    role = check.get("role")
    pattern = check.get("pattern")
    if role not in _ROLE_TO_EVENT_TYPE or not pattern:
        raise CheckSpecError(f"message_contains: нужны role (user/assistant) и pattern, получено {check}")
    event_type = _ROLE_TO_EVENT_TYPE[role]
    regex = re.compile(pattern)
    matches = [e for e in events if e.get("type") == event_type and regex.search(e.get("content", ""))]
    if matches:
        return {"type": "message_contains", "passed": True, "detail": f"совпало: {matches[0]['content']!r}"}
    total = sum(1 for e in events if e.get("type") == event_type)
    return {
        "type": "message_contains", "passed": False,
        "detail": f"паттерн {pattern!r} не найден ни в одном из {total} сообщений роли '{role}'",
    }


def _matching_stand_calls(events: list, event: str, handle: str) -> list:
    return [e for e in events if e.get("type") == event and e.get("handle") == handle]


def _check_event_field(check: dict, events: list) -> dict:
    event = check.get("event")
    handle = check.get("handle")
    path = check.get("path")
    has_equals = "equals" in check
    has_contains = "contains" in check
    if not event or not handle or not path or has_equals == has_contains:
        raise CheckSpecError(
            f"event_field: нужны event, handle, path и ровно одно из equals/contains, получено {check}"
        )
    matches = _matching_stand_calls(events, event, handle)
    if not matches:
        return {
            "type": "event_field", "passed": False,
            "detail": f"нет событий {event}/{handle} в трейсе",
        }
    index = check.get("index", len(matches) - 1)  # по умолчанию — последнее совпадение
    try:
        target = matches[index]
    except IndexError:
        return {
            "type": "event_field", "passed": False,
            "detail": f"index={index} вне диапазона — событий {event}/{handle} всего {len(matches)}",
        }
    try:
        actual = _get_path(target.get("response", {}), path)
    except (KeyError, IndexError, TypeError):
        return {
            "type": "event_field", "passed": False,
            "detail": f"путь '{path}' не найден в response события {event}/{handle}[{index}]",
        }
    if has_equals:
        expected = check["equals"]
        passed = actual == expected
        detail = f"{path} = {actual!r}" + ("" if passed else f", ожидалось {expected!r}")
    else:
        expected = check["contains"]
        try:
            passed = expected in actual
        except TypeError:
            passed = False
        detail = f"{path} = {actual!r}" + ("" if passed else f", ожидалось содержащим {expected!r}")
    return {"type": "event_field", "passed": passed, "detail": detail}


def _check_event_count(check: dict, events: list) -> dict:
    event = check.get("event")
    handle = check.get("handle")
    min_n = check.get("min")
    max_n = check.get("max")
    if not event or not handle or (min_n is None and max_n is None):
        raise CheckSpecError(f"event_count: нужны event, handle и хотя бы одно из min/max, получено {check}")
    count = len(_matching_stand_calls(events, event, handle))
    passed = (min_n is None or count >= min_n) and (max_n is None or count <= max_n)
    bounds = f"[{min_n if min_n is not None else '-'}, {max_n if max_n is not None else '-'}]"
    return {
        "type": "event_count", "passed": passed,
        "detail": f"{event}/{handle}: {count} событий, ожидался диапазон {bounds}",
    }


_CHECKERS = {
    "message_contains": _check_message_contains,
    "event_field": _check_event_field,
    "event_count": _check_event_count,
}


def evaluate_checks(exact_checks: list, events: list) -> list:
    results = []
    for check in exact_checks:
        checker = _CHECKERS.get(check.get("type"))
        if checker is None:
            raise CheckSpecError(f"неизвестный тип exact_checks: {check.get('type')!r} в {check}")
        results.append(checker(check, events))
    return results


def all_passed(results: list) -> bool:
    return all(r["passed"] for r in results)
