# AI Agent Engineering — Senior Track 2026

Практический репозиторий для изучения архитектуры и production-разработки
агентных систем. Основной принцип программы:

> Сначала реализовать механизм с нуля, проверить его отказоустойчивость,
> безопасность и качество — только затем использовать готовый framework.

## Основной маршрут

- [Программа в Markdown](docs/senior_ai_agent_engineer_2026.md)
- [Программа в PDF](docs/senior_ai_agent_engineer_2026.pdf)

Трек состоит из 14 модулей: от LLM Runtime, Tool Calling, Context и Memory
до собственного Agent Framework, durable execution, MCP/A2A/AG-UI,
security, evals и production capstone.

## Структура репозитория

```text
.
├── docs/
│   ├── senior_ai_agent_engineer_2026.md
│   └── senior_ai_agent_engineer_2026.pdf
├── lessons/
│   └── legacy/
│       ├── README.md
│       ├── lesson_1_basic_react_agent/
│       └── lesson_2_langgraph_react_agent/
├── pyproject.toml
└── uv.lock
```

Старые уроки сохранены в `lessons/legacy`, потому что они написаны по
предыдущей последовательности обучения. Новые материалы будут появляться
непосредственно в `lessons/module_XX_topic` по мере прохождения трека.
Пустые директории для всех 14 модулей заранее не создаются.

## Быстрый старт

Требования:

- Python 3.13+;
- [uv](https://docs.astral.sh/uv/);
- `OPENAI_API_KEY` только для live-запуска архивных примеров.

Установка зависимостей:

```bash
uv sync
```

Проверка CLI без API-вызова:

```bash
uv run python -m lessons.legacy.lesson_1_basic_react_agent --help
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent --help
```

Live-запуск:

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-5-mini"

uv run python -m lessons.legacy.lesson_1_basic_react_agent \
  "Сколько будет (17 + 25) * 3?"
```

## Архивные уроки

| Урок | Роль в новом треке | Когда открывать |
|---|---|---|
| [ReAct на чистом Python](lessons/legacy/lesson_1_basic_react_agent/README.md) | Исторический manual-JSON baseline, не замена native Tool Calling | Перед модулем 8 |
| [ReAct на LangGraph](lessons/legacy/lesson_2_langgraph_react_agent/README.md) | Вводный framework mapping, не замена собственного durable executor | После модулей 6–7 |

Подробнее: [описание legacy-раздела](lessons/legacy/README.md).

## Правило для новых уроков

Используется структура:

```text
lessons/module_XX_topic/
├── README.md
├── implementation/
├── tests/
└── artifacts/
```

Конкретный урок может быть компактнее, но его `README.md` должен содержать:

1. место в основном треке и prerequisites;
2. mental model, contracts и invariants;
3. самостоятельную реализацию механизма;
4. unit/contract/fault/security tests;
5. dataset, baseline и метрики;
6. framework mapping только после прохождения Gate;
7. команды запуска и ожидаемые результаты.

Отдельные `src/agent_runtime`, `tests` и
`projects/evidence_change_agent` следует создавать тогда, когда начнётся
работа над собственным framework и capstone, а не заранее.
