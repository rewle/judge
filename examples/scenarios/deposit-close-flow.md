---
stand_url: http://localhost:8767
compose_file: docker/open-deposit-compose.yml
max_turns: 4
exact_checks:
  - type: message_contains
    role: assistant
    pattern: "[Пп]одтвердите"
  - type: event_field
    event: stand_call
    handle: get_state
    path: deposit.status
    equals: closed
  - type: event_field
    event: stand_call
    handle: get_state
    path: deposit.opened_via
    equals: widget_confirm
  - type: event_count
    event: widget_confirm
    handle: auto_confirm
    min: 2
---
Цель диалога: пользователь сначала открывает вклад «СберВклад» (100 000 ₽,
36 месяцев, 19% годовых, списание с карты VISA), а затем в том же диалоге
просит закрыть этот же вклад.

Веди диалог так: в первой реплике попроси открыть вклад, назвав его имя,
сумму и срок; после того как вклад откроется (это сделает сам раннер
кнопкой в виджете, не пиши подтверждение текстом), в следующей реплике
попроси закрыть этот вклад. Оба подтверждения (открытия и закрытия)
выполняются отдельной кнопкой в виджете — не пиши подтверждение текстом
ни разу.

Семантически проверить: стенд не закрыл вклад раньше, чем он был открыт;
оба шага подтверждения (открытие и закрытие) прошли через отдельный шаг
«Пользователь [виджет]: …», а не сразу в ответ на текстовую реплику.
