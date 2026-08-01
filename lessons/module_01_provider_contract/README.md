# Практика 1.2: независимый от поставщика контракт

Практика относится к
[модулю 1, промпту 1.2](../../docs/senior_ai_agent_engineer_2026.md#промпт-12-независимый-от-поставщика-контракт)
и продолжает
[теоретический конспект](../../notes/module_01_provider_contract/module_01_provider_contract.md).
Здесь нет SDK или настоящего API: цель — сделать исполняемым собственный общий
контракт до написания адаптеров поставщиков.

## Карта файлов

```text
lessons/module_01_provider_contract/
├── README.md
├── implementation/
│   ├── contracts.py
│   ├── scripted_client.py
│   ├── stream_assembly.py
│   └── demo.py
└── tests/
    ├── contract_suite.py
    ├── test_ambiguous_email_recovery.py
    ├── test_contracts.py
    ├── test_scripted_client.py
    ├── test_stream_assembly.py
    └── test_integration.py
```

Файлы `__init__.py` обозначают пакеты и в поток выполнения не входят.

## Три маленьких компонента

1. `contracts.py` задаёт immutable `ModelRequest`, `ModelResponse`, варианты
   output, `Usage`, `ModelEvent`, `ModelClient` и категории ошибок.
2. `ScriptedModelClient` потребляет ровно один заранее заданный сценарий на
   вызов. Он не вызывает сеть, не делает retry и не исполняет `tool`.
3. `collect_stream` проверяет порядок событий, согласованность delta с
   завершёнными блоками и единственный терминальный ответ.

`demo.py` только собирает эти компоненты в две наблюдаемые трассы.

## Инварианты

- Raw provider data хранится как точные bytes с исходным `kind`; неизвестные
  block/event не отбрасываются. Bytes исключены из обычного `repr`.
- JSON-значения рекурсивно замораживаются. `StructuredOutput` хранит и
  `raw_json`, и разобранное значение; они обязаны совпадать.
- `usage=None` означает «не сообщено», а токены со значением `0` — сообщённый
  ноль. Равенство `total=input+output` не предполагается.
- `tool_requested` содержит `ToolCallOutput`, но `ModelClient` не имеет
  механизма исполнения `tool`.
- Частичные аргументы в `ToolArgumentsDelta` остаются строкой. Они становятся
  завершённым `ToolCallOutput` только после `OutputCompleted` и терминального
  `ResponseCompleted`.
- Оборванный поток возвращает `ModelStreamInterrupted` с точным наблюдённым
  префиксом и исходной причиной. `CancelledError` не оборачивается.
- `request_id` и `schema_id` сверяются на границе fake-клиента, чтобы сценарий
  другого запроса не мог быть принят как свой. Структурированный результат
  дополнительно проходит внедрённый deterministic schema validator; без него
  fake возвращает `ModelProtocolError`.

## Успешная и ошибочная трассы

Успешная трасса содержит text delta, части аргументов `tool`, неизвестное
событие, завершение обоих output, usage и терминальный ответ. Собранный
семантический ответ равен непотоковому ответу; provider events при этом
остаются доступными побайтово.

Ошибочная трасса обрывается после незавершённого `{"city":`. Никакого
`OutputCompleted` или `ResponseCompleted` не создаётся; вызывающий код видит
частичный префикс и `ModelTransportError` как `cause`.

## Fault-эксперимент инженерной защиты

`test_ambiguous_email_recovery.py` связывает честное отсутствие provider
`call_id` с состоянием Agent после crash вокруг `send_email`. Приложение
владеет отдельным стабильным `operation_id`; новый runtime сначала выполняет
reconciliation и различает `FOUND`, `TERMINALLY_ABSENT` и `UNKNOWN`.
Автоматический retry разрешён только для терминально не принятой операции.

Это test-only state machine: shared in-memory store имитирует durable state,
а scripted lookup — строгий terminal oracle. Эксперимент не доказывает
production transaction, генерацию ID или exactly-once delivery.

## Запуск

```bash
uv run --frozen python -m \
  lessons.module_01_provider_contract.implementation.demo

uv run --frozen pytest -q \
  lessons/module_01_provider_contract/tests

uv run --frozen pytest -q \
  lessons/module_01_provider_contract/tests/test_ambiguous_email_recovery.py
```

API token не нужен: `ScriptedModelClient` — детерминированная имитация LLM.

## Ограничения

В этой теме нет wire-адаптера, SSE parser, backpressure-эксперимента,
concurrent adapter, production retry/recovery subsystem, deadline, routing и
полного JSON Schema engine.
Проверяются `schema_id`, соответствие `raw_json` разобранному значению и
внедрённый validator. Полный JSON Schema engine не реализован; при настоящем
API его должен подключить адаптер. Один fake не
доказывает корректность SDK mapping и не закрывает Gate модуля 1: позже общий
набор контрактных тестов должны пройти два реальных адаптера.
