#!/usr/bin/env python3
"""Генератор скелета скилла — стартовая точка для тех, кто пишет SKILL.md
впервые и не знает формата. Готовый каркас проходит гейт 01 по конструкции
(валидный frontmatter, kebab-case name, триггер в description, структура),
кроме намеренных плейсхолдеров {{...}} в местах, которые обязательно нужно
заменить на реальный текст, — гейт 01 их поймает и явно перечислит, что
именно доделать (см. gates/g01_static.py, PLACEHOLDER_PATTERNS).

Использование:
    python3 new_skill.py my-skill-name
    python3 new_skill.py my-skill-name --dir ~/registry/skills
    python3 new_skill.py my-skill-name --tools Read,Bash --force
"""
import argparse
import re
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).parent))
from gates.g01_static import NAME_RE  # noqa: E402

TEMPLATE = Template('''---
name: $name
description: "{{Опиши одним-двумя предложениями, что делает скилл}}. Использовать когда {{опиши ситуацию, в которой ассистент должен сам выбрать этот скилл}}."
tools:
$tools_block
---

# $title

## Когда применять
{{Кратко: в какой ситуации пользователь обращается за этим скиллом}}

## Шаг 1. {{Название первого шага}}
{{Что конкретно делает ассистент на этом шаге}}

## Шаг 2. {{Название второго шага}}
{{Что конкретно делает ассистент на этом шаге}}

## Пример
{{Один сквозной пример входа и ожидаемого результата}}
''')


def _slugify(raw: str) -> str:
    slug = raw.strip().lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="имя скилла (будет приведено к kebab-case)")
    parser.add_argument("--dir", default=".", help="куда положить папку скилла (по умолчанию — текущая)")
    parser.add_argument(
        "--tools", default="Read",
        help="список инструментов через запятую (по умолчанию Read — самый безопасный дефолт)",
    )
    parser.add_argument("--force", action="store_true", help="перезаписать, если папка уже существует")
    args = parser.parse_args()

    name = _slugify(args.name)
    if not NAME_RE.match(name):
        print(f"Не удалось получить валидный kebab-case name из '{args.name}' (получилось: '{name}').", file=sys.stderr)
        sys.exit(1)

    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    tools_block = "\n".join(f"  - {t}" for t in tools) if tools else "  []"
    title = name.replace("-", " ").capitalize()

    target_dir = Path(args.dir).expanduser() / name
    target_file = target_dir / "SKILL.md"
    if target_file.exists() and not args.force:
        print(f"{target_file} уже существует. Передай --force, чтобы перезаписать.", file=sys.stderr)
        sys.exit(1)

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        TEMPLATE.substitute(name=name, title=title, tools_block=tools_block), encoding="utf-8"
    )

    print(f"Создано: {target_file}")
    print()
    print("Дальше:")
    print("  1. Замени все {{...}} на реальный текст — гейт 01 не пропустит, пока они есть,")
    print("     и явно перечислит, что именно осталось заполнить.")
    print("  2. Проверь: python3 run_gates.py --path " + str(target_dir))
    print("  3. Если tools: шире Read/Grep/Glob — добавь поле justification: (см. docs/02_permissions.md).")


if __name__ == "__main__":
    main()
