"""Гейт 01 — статические метрики. Детерминированный. См. docs/01_static.md.

Сообщения об ошибках намеренно многословны (с примером правильного
значения) — это первый гейт, который видит человек, впервые пишущий
SKILL.md, и часто единственная обратная связь перед тем, как разбираться
в остальной документации. См. docs/01_static.md, раздел "защита от
новичков"."""
import re
from pathlib import Path

import yaml

from gates.base import GateResult, PASS, FAIL

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
BANNED_NAME_WORDS = ("anthropic", "claude")
TRIGGER_WORDS = ("когда", "use when", "whenever", "использовать когда")
MAX_DESCRIPTION_LEN = 1024
MAX_BODY_LINES = 500

# Незаполненные плейсхолдеры из шаблона — если скилл написан впервые копией
# шаблона и подсказки не заменены на реальный текст. Проверяется отдельно
# от остальных правил, чтобы дать однозначное сообщение "ты забыл заменить
# X", а не смешивать это с содержательными претензиями к готовому тексту.
# TODO/FIXME/TBD ловим ТОЛЬКО в форме заметки-«доделать» (маркер + ':' или
# отдельной строкой/заголовком), а не как любое вхождение слова: иначе
# легитимный скилл ПРО эти маркеры (извлекатель техдолга, линтер) не может
# описать свою же предметную область, не завалив гейт — тот же класс ложного
# срабатывания, что negation-blind regex в гейте 06. "маркерами TODO, FIXME"
# или "TODO-комментарии" больше не считаются плейсхолдером; "TODO: доделать"
# и строка-заголовок из одного маркера — считаются.
_TODO_NOTE = re.compile(
    r"\b(?:TODO|FIXME|TBD)\b\s*:"                    # заметка вида "TODO: доделать"
    r"|^\s*[#>*\-]*\s*(?:TODO|FIXME|TBD)\s*$",       # строка/заголовок из одного маркера
    re.MULTILINE,
)
PLACEHOLDER_PATTERNS = (
    (re.compile(r"\{\{.*?\}\}"), "незаполненный плейсхолдер {{...}} из шаблона"),
    (re.compile(r"<[A-ZА-Я_]{3,}>"), "незаполненный плейсхолдер <ИМЯ_ПОЛЯ> из шаблона"),
    (_TODO_NOTE, "маркер-заметка TODO:/FIXME:/TBD («доделать») — незавершённый текст"),
    (
        re.compile(r"заполни(ть)?\s+эт[оу]|описани[ея]\s+скилла\s+здесь|your\s+description\s+here|name\s+of\s+the\s+skill\s+here"),
        "буквально скопированная подсказка шаблона вместо реального текста",
    ),
)
GENERIC_NAMES = {"my-skill", "example-skill", "new-skill", "test-skill", "untitled", "untitled-skill", "skill-name"}


def _mask_code(text: str) -> str:
    """Заменяет содержимое fenced-блоков и inline code-span'ов пробелами,
    СОХРАНЯЯ переносы строк — так номера строк совпадают с исходным телом
    (для локаций в сообщениях), а примеры внутри кода (regex-заглушки,
    <плейсхолдеры> в демо) не считаются реальным текстом. Пришло на смену
    прежнему re.sub(..., "") — тот схлопывал строки и ломал нумерацию."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group())
    text = re.sub(r"```.*?```", blank, text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", blank, text)
    return text


def _read_skill(skill_path: Path):
    md_files = list(skill_path.glob("SKILL.md"))
    if not md_files:
        return None, None, None
    text = md_files[0].read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, None, text
    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        # Блок --- ... --- есть, но это невалидный YAML (частый случай у
        # новичков: незаэкранированные : { } [ ] в значении без кавычек).
        # Возвращаем None как при отсутствующем frontmatter — вызывающие
        # гейты уже умеют его обрабатывать, не роняясь; check() в этом
        # модуле различает "нет блока" и "блок есть, но битый" отдельно.
        return None, None, text
    body = parts[2]
    return frontmatter, body, text


def check(skill_path: Path) -> GateResult:
    frontmatter, body, text = _read_skill(skill_path)
    if text is None:
        return GateResult(FAIL, "SKILL.md не найден", {})
    if frontmatter is None:
        if text.startswith("---") and text.count("---") >= 2:
            return GateResult(
                FAIL,
                "блок --- ... --- есть, но это невалидный YAML. Частая причина: "
                "спецсимволы (: {{ }} [ ] \" ') в значении без кавычек — оберни строку "
                "в кавычки, например description: \"текст с {{плейсхолдером}} и двоеточием: вот так\"",
                {},
            )
        return GateResult(FAIL, "нет frontmatter (--- ... ---) в начале файла", {})

    errors = []

    name = frontmatter.get("name", "")
    if not NAME_RE.match(name):
        errors.append(
            f"name '{name}' не kebab-case / длиннее 64 симв. "
            f"(пример правильного: 'my-skill-name' — строчные латинские буквы, цифры, дефис)"
        )
    if any(w in name.lower() for w in BANNED_NAME_WORDS):
        errors.append(f"name '{name}' содержит запрещённое слово (anthropic/claude)")
    if name.lower() in GENERIC_NAMES:
        errors.append(
            f"name '{name}' похоже на не переименованный шаблон — дай содержательное имя, "
            f"отражающее конкретную задачу скилла (например 'csv-column-renamer', не 'my-skill')"
        )

    description = frontmatter.get("description", "")
    if not description:
        errors.append("description пустой")
    elif len(description) > MAX_DESCRIPTION_LEN:
        errors.append(f"description длиннее {MAX_DESCRIPTION_LEN} символов")
    if description and not any(t in description.lower() for t in TRIGGER_WORDS):
        errors.append(
            "description не содержит триггера использования — добавь явное 'Использовать когда...' "
            "или 'Use when...'. Пример: 'Делает X. Использовать когда пользователь просит Y.'"
        )

    body_lines = (body or "").splitlines()
    if len(body_lines) > MAX_BODY_LINES:
        errors.append(f"тело скилла длиннее {MAX_BODY_LINES} строк ({len(body_lines)})")

    # Пути с обратным слешем и плейсхолдеры ищем вне code span'ов/блоков —
    # иначе ложно ловим regex-примеры вида `\d{2}\.\d{2}` в документации
    # (см. golden-skill) или технические заглушки вида `<папка>` в примерах.
    # Маскируем код пробелами (не вырезаем), чтобы номера строк в сообщениях
    # совпадали с исходным файлом — новичок должен видеть, ГДЕ проблема, а не
    # получать одно и то же красное сообщение на каждую итерацию (см. UX-прогон
    # роли «нуб»).
    prose = _mask_code(body or "")
    body_start = len(text) - len(body or "")

    def _line_of(local_offset: int) -> int:
        return text.count("\n", 0, body_start + local_offset) + 1

    bs = re.search(r"[a-zA-Z]:\\|\.\\\w|\\\\", prose)
    if bs:
        errors.append(
            f"в теле (вне code span'ов) найдены пути с обратным слешем "
            f"(строка {_line_of(bs.start())}) — использовать только /"
        )

    # Каждое совпадение — со своей локацией (поле description или номер строки
    # в теле), и перечисляем ВСЕ, а не по одному: иначе новичок чинит первое,
    # снова получает тот же фейл и не понимает, что осталось ещё.
    placeholder_hits = []
    for pattern, hint in PLACEHOLDER_PATTERNS:
        locations = []
        if pattern.search(description):
            locations.append("поле description")
        locations += [f"тело, строка {_line_of(m.start())}" for m in pattern.finditer(prose)]
        if locations:
            placeholder_hits.append(f"{hint} ({', '.join(locations)})")
    if placeholder_hits:
        errors.append(
            "похоже, скилл содержит незаполненные плейсхолдеры шаблона: "
            + "; ".join(placeholder_hits)
            + ". Замени их на реальный текст в перечисленных местах перед проверкой."
        )

    if errors:
        return GateResult(FAIL, "; ".join(errors), {"errors": errors})
    return GateResult(PASS, "статические метрики в норме", {})
