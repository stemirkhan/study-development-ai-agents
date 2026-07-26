# Архивные уроки

В этом каталоге сохранены два запускаемых урока из предыдущего плана.
Они остаются полезными как сравнительные примеры, но не определяют порядок
и архитектурные требования нового трека.

## Как они соотносятся с программой 2026

| Урок | Что показывает | Ограничение | Место в новом треке |
|---|---|---|---|
| [ReAct на чистом Python](lesson_1_basic_react_agent/README.md) | Явный цикл Reasoning → Action → Observation и ручной JSON-протокол | Нет provider-native tool calling, durability и production policy layer | Исторический baseline перед модулем 8 |
| [ReAct на LangGraph](lesson_2_langgraph_react_agent/README.md) | State, nodes, edges, reducers и условную маршрутизацию | Не реализует собственный Graph Executor, checkpoints и side-effect safety | Framework mapping после модулей 6–7 |

Второй урок импортирует protocol, prompt и tools первого, поэтому уроки
перемещены вместе.

## Запуск

Из корня репозитория:

```bash
uv sync

uv run python -m lessons.legacy.lesson_1_basic_react_agent --help
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent --help
```

Для live-вызовов модели установите `OPENAI_API_KEY`. Локальные protocol и
calculator можно импортировать и тестировать без сетевого доступа.

## Что считать актуальным

Актуальные требования, порядок модулей и Gates находятся в
[основной программе](../../docs/senior_ai_agent_engineer_2026.md).
Если архивный пример расходится с ней, приоритет имеет основная программа.
