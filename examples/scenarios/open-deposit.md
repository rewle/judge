---
stand_url: http://localhost:8767
compose_file: docker/open-deposit-compose.yml
max_turns: 2
exact_checks:
  - type: message_contains
    role: assistant
    pattern: "[Пп]одтвердите"
  - type: event_field
    event: stand_call
    handle: get_state
    path: deposit.status
    equals: opened
  - type: event_field
    event: stand_call
    handle: get_state
    path: deposit.opened_via
    equals: widget_confirm
  - type: event_count
    event: widget_confirm
    handle: auto_confirm
    min: 1
---
Цель диалога: пользователь хочет открыть вклад «СберВклад» на 100 000 ₽
на 36 месяцев под 19% годовых со списанием с карты VISA.

Веди диалог так: в первой же реплике попроси открыть этот вклад, назвав
его имя, сумму и срок. Подтверждение операции выполняется отдельной
кнопкой в виджете — не пиши подтверждение текстом, шаг подтверждения
выполнит сам раннер.

Семантически проверить: стенд озвучил условия вклада (сумма, срок,
ставка) и попросил подтверждение ДО открытия; фактическое открытие
произошло только после шага «Пользователь [виджет]: …» (детерминированное
подтверждение кнопкой), а не сразу в ответ на первую текстовую реплику.
