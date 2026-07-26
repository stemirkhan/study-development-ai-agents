# ReAct-агент на LangGraph

> **Статус:** архивный урок из предыдущего плана. Проходите его после
> модулей 6–7 нового трека как вводный framework mapping, но не как замену
> реализации собственного durable Graph Executor.

## Цель урока

После изучения материала вы будете понимать:

- зачем представлять агентный цикл в виде графа;
- что в LangGraph означают State, Node и Edge;
- чем обычное ребро отличается от условного;
- как `TypedDict` описывает состояние агента;
- зачем полю `messages` нужен reducer;
- как узлы возвращают частичные обновления состояния;
- как граф создаёт цикл `LLM → Tool → LLM`;
- как `START`, `END`, `max_steps` и `recursion_limit` управляют выполнением.

В первом уроке ReAct-цикл был реализован обычным циклом `for`.
В этом уроке тот же основной workflow выражен через `StateGraph` из LangGraph.

Перед изучением материала рекомендуется пройти
[первый урок](../lesson_1_basic_react_agent/README.md), потому что второй урок
переиспользует его:

- системный промпт;
- JSON-протокол;
- классы `ToolCall` и `FinalAnswer`;
- калькулятор и симулированный поиск;
- функцию `execute_tool()`.

## Что изменилось по сравнению с первым уроком

В первом уроке оркестрация записана как последовательный Python-код:

```python
for step in range(max_steps):
    action = call_llm()

    if action является FinalAnswer:
        return action.answer

    observation = execute_tool(action)
```

Программист сам управляет каждой итерацией:

- вызывает LLM;
- проверяет результат;
- запускает инструмент;
- добавляет Observation;
- возвращается в начало цикла;
- завершает выполнение.

В LangGraph алгоритм описывается иначе:

1. Данные агента помещаются в общее состояние — State.
2. Каждый этап становится отдельным узлом — Node.
3. Переходы между этапами задаются рёбрами — Edge.
4. Условное ребро выбирает следующий узел по текущему состоянию.
5. LangGraph сам переносит State между узлами до достижения `END`.

Сравнение двух реализаций:

| Чистый Python | LangGraph |
|---|---|
| Переменные внутри функции | Поля общего State |
| Тело цикла `for` | Набор Node |
| `if` и `continue` | Conditional Edge |
| Следующая итерация | Ребро `tool → llm` |
| `return` | Переход к `END` |
| Ручное обновление списка | Reducer поля `messages` |

LangGraph не делает модель умнее и не выбирает инструменты вместо неё.
Он предоставляет явную структуру для управления состоянием и маршрутами
выполнения.

## Три основных понятия LangGraph

### 1. State

State — общее состояние графа.

В нём хранятся данные, которые должны быть доступны разным узлам:

```python
class AgentState(TypedDict):
    messages: Annotated[list[ChatMessage], operator.add]
    step_count: int
    current_action: AgentAction | None
    max_steps: int
    final_answer: str | None
```

Для нашего агента State содержит:

| Поле | Назначение |
|---|---|
| `messages` | Полная история диалога с LLM |
| `step_count` | Количество уже выполненных вызовов модели |
| `current_action` | Текущий `ToolCall`, `FinalAnswer` или `None` |
| `max_steps` | Максимально разрешённое количество вызовов LLM |
| `final_answer` | Итоговая строка, которую вернёт агент |

State можно представить как общую память workflow. Каждый узел получает
актуальное состояние, читает нужные поля и возвращает изменения.

`TypedDict` и `Annotated` помогают статической типизации и сообщают LangGraph
схему состояния, но не выполняют строгую runtime-валидацию значений как
Pydantic-модель.

### 2. Node

Node — функция, выполняющая один этап workflow.

Упрощённая форма узла:

```python
def some_node(state: AgentState) -> AgentStateUpdate:
    ...
    return {
        "step_count": state["step_count"] + 1,
    }
```

Узел:

1. Получает текущее состояние.
2. Выполняет ограниченную операцию.
3. Возвращает частичное обновление State.

В графе урока четыре узла:

| Node | Ответственность |
|---|---|
| `llm` | Вызывает модель и разбирает её JSON-ответ |
| `tool` | Выполняет Python-инструмент и создаёт Observation |
| `final` | Сохраняет финальный ответ |
| `step_limit` | Безопасно останавливает агента по лимиту |

### 3. Edge

Edge — переход от одного узла к другому.

Обычное ребро всегда ведёт по одному маршруту:

```python
builder.add_edge(START, "llm")
builder.add_edge("tool", "llm")
builder.add_edge("final", END)
```

Условное ребро выбирает маршрут во время выполнения:

```python
builder.add_conditional_edges(
    "llm",
    route_after_llm,
    {
        "tool": "tool",
        "retry": "llm",
        "final": "final",
        "step_limit": "step_limit",
    },
)
```

Решение зависит от содержимого `current_action` и значения `step_count`.

## Архитектура графа

Общая схема:

```text
                         ┌──────── retry ────────┐
                         │                       │
START ──→ llm ───────────┼── tool ──────────────┘
           │             │
           │             ├── final ──────→ END
           │             │
           └─────────────┴── step_limit ─→ END
```

Основной успешный маршрут с одним инструментом:

```text
START
  ↓
llm
  ↓ ToolCall
tool
  ↓ Observation
llm
  ↓ FinalAnswer
final
  ↓
END
```

Если LLM вернула невалидный JSON:

```text
llm
  ↓ current_action = None
retry
  ↓
llm
```

Если модель исчерпала лимит вызовов:

```text
llm
  ↓ step_count >= max_steps
step_limit
  ↓
END
```

## Как описано состояние

Состояние находится в [`state.py`](./state.py).

### Сообщение для OpenAI API

Отдельный `TypedDict` описывает формат сообщения:

```python
class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
```

Поле `role` может содержать только одно из двух значений:

- `"user"`;
- `"assistant"`.

Функция `message()` помогает создавать такие объекты:

```python
message("user", "Посчитай 12 * 8")
```

Результат:

```python
{
    "role": "user",
    "content": "Посчитай 12 * 8",
}
```

### Полное состояние

`AgentState` описывает объект, существующий во время выполнения всего графа:

```python
class AgentState(TypedDict):
    messages: Annotated[list[ChatMessage], operator.add]
    step_count: int
    current_action: AgentAction | None
    max_steps: int
    final_answer: str | None
```

Начальное состояние создаётся перед запуском:

```python
initial_state: AgentState = {
    "messages": [message("user", question)],
    "step_count": 0,
    "current_action": None,
    "max_steps": max_steps,
    "final_answer": None,
}
```

До первого узла:

```text
messages       = [вопрос пользователя]
step_count     = 0
current_action = None
max_steps      = 6
final_answer   = None
```

### Частичное обновление

Узел не обязан возвращать всё состояние целиком.

Для этого определён `AgentStateUpdate`:

```python
class AgentStateUpdate(TypedDict, total=False):
    messages: list[ChatMessage]
    step_count: int
    current_action: AgentAction | None
    max_steps: int
    final_answer: str | None
```

Параметр `total=False` означает, что все поля необязательны.

Например, узел `final` меняет только одно поле:

```python
return {"final_answer": action.answer}
```

Остальные значения остаются прежними.

## Reducer поля `messages`

Одна из самых важных строк урока:

```python
messages: Annotated[list[ChatMessage], operator.add]
```

`operator.add` — reducer этого поля.

Reducer определяет, как LangGraph должен объединить старое значение поля
с новым значением, возвращённым узлом.

Пусть в State уже находится:

```python
messages = [
    {"role": "user", "content": "Сколько будет 12 * 8?"},
]
```

Узел `llm` возвращает только новое сообщение:

```python
{
    "messages": [
        {"role": "assistant", "content": "{\"action\": \"tool\", ...}"},
    ],
}
```

Reducer выполняет операцию:

```python
old_messages + new_messages
```

После обновления:

```python
messages = [
    {"role": "user", "content": "Сколько будет 12 * 8?"},
    {"role": "assistant", "content": "{\"action\": \"tool\", ...}"},
]
```

Без reducer новое значение заменило бы старый список, и вопрос пользователя
исчез бы из истории.

### Почему узлы возвращают только новые сообщения

В `llm_node()` используется:

```python
new_messages = [message("assistant", raw_response)]

return {
    "messages": new_messages,
    ...
}
```

Узел не должен возвращать старую историю вместе с новой:

```python
# Неправильно при reducer operator.add:
return {
    "messages": state["messages"] + new_messages,
}
```

Reducer ещё раз добавил бы возвращённый список к старому, и сообщения
продублировались бы.

Правило:

```text
Node возвращает новые сообщения
        ↓
Reducer объединяет их со старой историей
```

## Внешние зависимости не хранятся в State

Функция `build_react_graph()` принимает:

```python
def build_react_graph(
    *,
    client: OpenAI,
    model: str,
    verbose: bool = True,
) -> Any:
```

`client`, `model` и `verbose` используются вложенными функциями
`llm_node()` и `tool_node()` через замыкание.

Они не помещаются в `AgentState`, потому что это зависимости выполнения,
а не изменяемые данные конкретного запуска:

| Данные | Где находятся |
|---|---|
| История сообщений | В State |
| Счётчик шагов | В State |
| Текущее действие | В State |
| OpenAI-клиент | В замыкании узла |
| Название модели | В замыкании узла |
| Режим вывода логов | В замыкании узла |

Так состояние остаётся простым и пригодным для передачи между узлами.

## Как создаётся граф

### Шаг 1. Создание builder

```python
builder = StateGraph(AgentState)
```

`StateGraph` получает схему состояния и создаёт builder будущего workflow.

На этом этапе граф ещё нельзя запускать.

### Шаг 2. Регистрация узлов

```python
builder.add_node("llm", llm_node)
builder.add_node("tool", tool_node)
builder.add_node("final", final_node)
builder.add_node("step_limit", step_limit_node)
```

Первый аргумент — имя узла в графе.
Второй аргумент — Python-функция, которая будет выполняться.

### Шаг 3. Добавление обычных рёбер

```python
builder.add_edge(START, "llm")
builder.add_edge("tool", "llm")
builder.add_edge("final", END)
builder.add_edge("step_limit", END)
```

Эти переходы не требуют принятия решения:

- выполнение всегда начинается с `llm`;
- после инструмента управление всегда возвращается к `llm`;
- после `final` и `step_limit` граф всегда завершается.

### Шаг 4. Добавление условного ребра

```python
builder.add_conditional_edges(
    "llm",
    route_after_llm,
    {
        "tool": "tool",
        "retry": "llm",
        "final": "final",
        "step_limit": "step_limit",
    },
)
```

После узла `llm` LangGraph:

1. Вызывает `route_after_llm(state)`.
2. Получает строку маршрута.
3. Находит эту строку в словаре переходов.
4. Передаёт управление соответствующему узлу.

Например:

```text
route_after_llm(state) → "tool"
                         ↓
словарь маршрутов       → "tool" Node
```

### Шаг 5. Компиляция

```python
return builder.compile()
```

`compile()` проверяет структуру и создаёт исполняемый граф.

Разница:

```text
StateGraph builder  → описание узлов и рёбер
Compiled graph      → объект, который можно вызвать через invoke()
```

## Как работает маршрутизация

Функция `route_after_llm()` анализирует состояние:

```python
def route_after_llm(state: AgentState) -> Route:
    action = state["current_action"]

    if isinstance(action, FinalAnswer):
        return "final"

    if state["step_count"] >= state["max_steps"]:
        return "step_limit"

    if isinstance(action, ToolCall):
        return "tool"

    return "retry"
```

Возможные маршруты:

| Условие | Результат | Следующий Node |
|---|---|---|
| Получен `FinalAnswer` | `"final"` | `final` |
| Достигнут `max_steps` | `"step_limit"` | `step_limit` |
| Получен `ToolCall` | `"tool"` | `tool` |
| `current_action is None` | `"retry"` | снова `llm` |

Порядок проверок важен.

`FinalAnswer` проверяется раньше лимита. Если модель сформировала итоговый ответ
на последнем разрешённом вызове, он будет принят.

Лимит проверяется раньше `ToolCall`. Если на последнем разрешённом вызове модель
попросила инструмент, граф остановится и не начнёт новый незавершённый цикл.

Это небольшое отличие от реализации первого урока. В обычном цикле инструмент,
запрошенный на последней итерации, успевает выполниться, после чего функция
возвращает сообщение о лимите. В текущем графе последний `ToolCall` не
выполняется: router сразу направляет состояние в `step_limit`.

## Разбор узлов

### Node `llm`

`llm_node()` реализует этап Reasoning:

```python
next_step = state["step_count"] + 1

response = client.responses.create(
    model=model,
    instructions=SYSTEM_PROMPT,
    input=state["messages"],
)

raw_response = response.output_text
```

Модель получает всю накопленную историю из State.

Затем ответ добавляется в список новых сообщений:

```python
new_messages = [message("assistant", raw_response)]
```

JSON разбирается функцией из первого урока:

```python
action = parse_llm_response(raw_response)
```

Узел возвращает:

```python
return {
    "messages": new_messages,
    "step_count": next_step,
    "current_action": action,
}
```

После этого условное ребро определяет следующий этап.

### Node `tool`

`tool_node()` реализует этап Acting:

```python
action = state["current_action"]

if not isinstance(action, ToolCall):
    raise TypeError("tool_node ожидает ToolCall")
```

Проверка типа защищает контракт узла: `tool` должен запускаться только
для `ToolCall`.

Затем вызывается инструмент:

```python
observation = execute_tool(action)
```

Результат превращается в сообщение:

```python
observation_message = json.dumps(
    {
        "tool_name": action.name,
        "result": observation,
    },
    ensure_ascii=False,
)
```

Частичное обновление:

```python
return {
    "messages": [
        message("user", f"Observation: {observation_message}")
    ],
    "current_action": None,
}
```

`current_action` сбрасывается, потому что вызов инструмента уже обработан.
Reducer добавляет Observation к истории.

После узла `tool` обычное ребро возвращает граф к `llm`.

### Node `final`

`final_node()` получает готовый ответ:

```python
action = state["current_action"]

if not isinstance(action, FinalAnswer):
    raise TypeError("final_node ожидает FinalAnswer")

return {"final_answer": action.answer}
```

Узел ничего больше не вызывает. Он переносит ответ из `current_action`
в отдельное поле `final_answer`.

После этого ребро ведёт к `END`.

### Node `step_limit`

`step_limit_node()` формирует контролируемый ответ:

```python
answer = (
    f"Агент остановлен после {state['max_steps']} шагов: "
    "модель не сформировала финальный ответ."
)
```

Узел:

- добавляет сообщение об остановке в историю;
- записывает `FinalAnswer` в `current_action`;
- сохраняет строку в `final_answer`.

После него граф также переходит к `END`.

## Пошаговый разбор примера

Вопрос:

```text
Сколько будет (17 + 5) * 3?
```

### Шаг 1. Начальное состояние

```text
messages       = [User: "Сколько будет (17 + 5) * 3?"]
step_count     = 0
current_action = None
max_steps      = 6
final_answer   = None
```

`START` передаёт управление узлу `llm`.

### Шаг 2. Первый вызов LLM

Модель возвращает:

```json
{
  "action": "tool",
  "tool_name": "calculator",
  "arguments": {
    "expression": "(17 + 5) * 3"
  }
}
```

`parse_llm_response()` создаёт:

```python
ToolCall(
    name="calculator",
    arguments={"expression": "(17 + 5) * 3"},
)
```

Обновлённые поля:

```text
messages       += [Assistant: JSON с ToolCall]
step_count      = 1
current_action  = ToolCall(...)
```

### Шаг 3. Выбор маршрута

`route_after_llm()` видит `ToolCall`:

```python
return "tool"
```

LangGraph переходит в `tool_node()`.

### Шаг 4. Выполнение инструмента

Python вызывает:

```python
calculator(expression="(17 + 5) * 3")
```

Результат:

```text
66
```

`tool_node()` возвращает новое сообщение:

```text
Observation: {"tool_name": "calculator", "result": "66"}
```

Reducer добавляет его к истории, а `current_action` сбрасывается:

```text
messages       += [User: Observation]
current_action  = None
```

### Шаг 5. Возврат в `llm`

Ребро:

```python
builder.add_edge("tool", "llm")
```

создаёт следующий виток ReAct-цикла.

Модель видит:

1. Исходный вопрос.
2. Собственный запрос калькулятора.
3. Observation со значением `66`.

### Шаг 6. Финальный ответ

Модель возвращает:

```json
{
  "action": "final",
  "answer": "Результат вычисления: 66."
}
```

Состояние после второго вызова:

```text
step_count     = 2
current_action = FinalAnswer(answer="Результат вычисления: 66.")
```

Router выбирает `"final"`.

### Шаг 7. Завершение

`final_node()` записывает:

```text
final_answer = "Результат вычисления: 66."
```

Ребро `final → END` завершает workflow.

## Как обрабатывается некорректный JSON

Если модель нарушила протокол:

```text
Для ответа вызову калькулятор.
```

`parse_llm_response()` выбрасывает `AgentProtocolError`.

`llm_node()` превращает ошибку в Observation:

```python
observation = (
    f"Ошибка протокола: {error}. "
    "Верни корректный JSON по заданной схеме."
)
```

В одном обновлении добавляются два сообщения:

```python
[
    message("assistant", raw_response),
    message("user", f"Observation: {observation}"),
]
```

При этом:

```python
current_action = None
```

Если лимит ещё не достигнут, router возвращает `"retry"`, и граф снова
вызывает `llm`.

Так ошибка формата становится частью управляемого агентного цикла, а не
аварийным завершением программы.

Другие ожидаемые ошибки инструментов обрабатываются функцией `execute_tool()`
из первого урока. Например, неизвестное имя инструмента, неверные аргументы
или деление на ноль превращаются в текст Observation.

При этом реализация намеренно не перехватывает все исключения. Сетевая ошибка
OpenAI API, ошибка конфигурации или программная ошибка внутри Node завершит
`invoke()` исключением. Для production-системы такие случаи обычно дополняют
таймаутами, журналированием и контролируемыми повторными попытками.

## Два ограничения выполнения

В реализации используются две разные защиты.

### `max_steps`

`max_steps` — бизнес-ограничение агента.

Оно считает вызовы LLM:

```python
next_step = state["step_count"] + 1
```

Лимит нужен, чтобы контролировать:

- количество API-запросов;
- стоимость;
- время выполнения;
- повторные ошибки протокола;
- циклический выбор инструментов.

### `recursion_limit`

При запуске графа дополнительно передаётся:

```python
graph.invoke(
    initial_state,
    {"recursion_limit": max_steps * 3 + 5},
)
```

`recursion_limit` — страховка движка LangGraph для циклического графа.
Он ограничивает число внутренних шагов выполнения графа, а не только вызовов
модели.

Один вызов LLM может сопровождаться несколькими узлами:

```text
llm → tool → llm → final
```

Поэтому значение `recursion_limit` больше `max_steps`.

Эти ограничения не заменяют друг друга:

| Ограничение | Что контролирует |
|---|---|
| `max_steps` | Количество вызовов LLM по правилам агента |
| `recursion_limit` | Общую глубину циклического выполнения графа |

## Запуск графа через `invoke`

После компиляции граф запускается так:

```python
result = graph.invoke(
    initial_state,
    {"recursion_limit": max_steps * 3 + 5},
)
```

`invoke()`:

1. Принимает начальный State.
2. Запускает маршрут от `START`.
3. Передаёт обновлённое состояние между узлами.
4. Следует обычным и условным рёбрам.
5. Возвращает итоговый State после достижения `END`.

В текущей реализации `run_langgraph_agent()` заново вызывает
`build_react_graph()` для каждого запуска. Скомпилированный граф не кешируется,
а состояние не сохраняется между отдельными вызовами функции.

Из результата извлекается ответ:

```python
final_answer = result["final_answer"]

if final_answer is None:
    raise RuntimeError("граф завершился без final_answer")

return final_answer
```

Проверка `None` защищает внешний контракт функции:
`run_langgraph_agent()` должна всегда вернуть строку.

## Что переиспользуется из первого урока

LangGraph-версия не дублирует уже написанную предметную логику:

```python
from lessons.legacy.lesson_1_basic_react_agent.prompts import SYSTEM_PROMPT
from lessons.legacy.lesson_1_basic_react_agent.protocol import (
    AgentProtocolError,
    FinalAnswer,
    ToolCall,
    parse_llm_response,
)
from lessons.legacy.lesson_1_basic_react_agent.tools import execute_tool
```

Разделение ответственности:

| Часть | Урок |
|---|---|
| Описание доступных действий для LLM | Урок 1 |
| Проверка JSON-протокола | Урок 1 |
| Реестр и выполнение инструментов | Урок 1 |
| State, Node, Edge и маршрутизация | Урок 2 |

Это показывает важную идею: переход на LangGraph меняет оркестрацию,
но не требует переписывать инструменты и протокол агента.

Observation по-прежнему передаётся как обычное сообщение с ролью `"user"`.
В уроке не используются нативные сообщения инструментов, готовый `ToolNode`
или встроенный ReAct-помощник LangGraph: все ключевые переходы специально
описаны вручную для наглядности.

## Структура урока

```text
lesson_2_langgraph_react_agent/
├── __init__.py
├── __main__.py
├── cli.py
├── graph.py
├── state.py
├── langgraph_flow.html
└── README.md
```

Назначение файлов:

| Файл | Назначение |
|---|---|
| [`graph.py`](./graph.py) | Узлы, рёбра, router, сборка и запуск графа |
| [`state.py`](./state.py) | Типы `AgentState`, `AgentStateUpdate` и сообщений |
| [`cli.py`](./cli.py) | Аргументы командной строки и запуск агента |
| [`__main__.py`](./__main__.py) | Поддержка запуска через `python -m` |
| [`__init__.py`](./__init__.py) | Публичные функции пакета |
| [`langgraph_flow.html`](./langgraph_flow.html) | Интерактивная схема StateGraph |

## Интерактивная визуализация

Файл [`langgraph_flow.html`](./langgraph_flow.html) пошагово показывает:

1. Общее состояние `AgentState`.
2. Регистрацию Node в `StateGraph`.
3. Переход `START → llm`.
4. Работу Conditional Edge.
5. Выполнение `tool` Node.
6. Объединение сообщений через reducer.
7. Цикл `tool → llm`.
8. Завершение через `final` или `step_limit`.

Для просмотра откройте HTML-файл в браузере.
В Linux это можно сделать командой:

```bash
xdg-open lessons/legacy/lesson_2_langgraph_react_agent/langgraph_flow.html
```

## Запуск

Установите API-ключ:

```bash
export OPENAI_API_KEY="ваш-ключ"
```

Запустите LangGraph-агента:

```bash
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent \
  "Что такое ReAct и сколько будет (17 + 5) * 3?"
```

Изменение максимального количества вызовов LLM:

```bash
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent \
  "Сколько будет 12 * 8?" \
  --max-steps 4
```

Изменение модели:

```bash
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent \
  "Что такое LangGraph?" \
  --model gpt-5-mini
```

Если вопрос не передан аргументом, CLI запросит его интерактивно:

```bash
uv run python -m lessons.legacy.lesson_2_langgraph_react_agent
```

## Как тестировать без обращения к API

`OpenAI`-клиент передаётся в функцию как зависимость:

```python
run_langgraph_agent(
    question,
    client=client,
    model=model,
)
```

Поэтому в тесте можно использовать поддельный клиент с заранее заданными
ответами:

```python
from types import SimpleNamespace

from lessons.legacy.lesson_2_langgraph_react_agent.graph import run_langgraph_agent


class FakeResponses:
    def __init__(self) -> None:
        self.outputs = iter([
            (
                '{"action":"tool","tool_name":"calculator",'
                '"arguments":{"expression":"6 * 7"}}'
            ),
            '{"action":"final","answer":"Ответ: 42"}',
        ])

    def create(self, **kwargs):
        return SimpleNamespace(output_text=next(self.outputs))


client = SimpleNamespace(responses=FakeResponses())

answer = run_langgraph_agent(
    "Сколько будет 6 * 7?",
    client=client,
    model="fake-model",
    verbose=False,
)

assert answer == "Ответ: 42"
```

Такой тест проверяет:

- прохождение по условному ребру;
- вызов инструмента;
- возврат из `tool` в `llm`;
- накопление истории;
- переход в `final`;
- получение итогового ответа.

## Когда граф удобнее обычного цикла

Обычного Python-цикла достаточно, если:

- workflow короткий и линейный;
- возможных переходов мало;
- состояние используется только внутри одной функции;
- не требуются сложные ветвления.

Граф становится особенно полезным, когда появляются:

- несколько типов инструментов;
- отдельные узлы проверки и модерации;
- ветки подтверждения пользователем;
- повторные попытки с разными правилами;
- несколько вариантов завершения;
- длинные циклические workflow;
- необходимость наблюдать маршрут выполнения;
- сохранение и восстановление состояния.

Визуальная структура графа помогает отделить:

```text
что хранит агент     → State
что делает агент     → Node
куда он идёт дальше  → Edge
```

## Что LangGraph не делает автоматически

Само использование `StateGraph` не решает все задачи production-агента.

В текущем уроке нет:

- постоянного хранилища состояния;
- checkpoint и возобновления после перезапуска;
- потоковой выдачи результатов;
- асинхронного выполнения;
- параллельных узлов;
- нативного tool calling;
- строгой проверки аргументов по JSON Schema;
- таймаутов и повторных попыток для внешних API;
- подтверждения опасных действий пользователем.

Также LangGraph не гарантирует, что LLM:

- выберет правильный инструмент;
- передаст корректные аргументы;
- правильно интерпретирует Observation;
- сформирует финальный ответ за разрешённое число шагов.

Фреймворк управляет workflow, но качество решений по-прежнему зависит
от модели, промпта, инструментов и защитных механизмов.

## Типичные ошибки при работе со StateGraph

### Возвращать всё состояние из каждого Node

Узлам обычно достаточно возвращать изменённые поля.
Полное копирование State усложняет код и повышает риск случайной перезаписи.

### Возвращать старые сообщения вместе с новыми

При reducer `operator.add` это создаст дубликаты.
Node должен возвращать только добавляемые сообщения.

### Хранить несериализуемые зависимости в State

Клиенты API и конфигурацию выполнения удобнее передавать через замыкание,
dependency injection или runtime context.

### Забыть маршрут завершения

Циклический граф должен иметь достижимый путь к `END`.
Иначе выполнение завершится только по защитному лимиту или ошибке.

### Считать `recursion_limit` заменой `max_steps`

`recursion_limit` защищает движок графа, а `max_steps` выражает правило
и бюджет самого агента.

### Не сбрасывать обработанное действие

После выполнения инструмента старый `ToolCall` не должен оставаться текущей
командой. В уроке `tool_node()` возвращает:

```python
{"current_action": None}
```

## Главные выводы

1. LangGraph выражает агентную оркестрацию через State, Node и Edge.
2. State хранит данные, доступные всем узлам текущего запуска.
3. Node читает State и возвращает частичное обновление.
4. Обычное ребро задаёт фиксированный переход.
5. Conditional Edge выбирает маршрут по текущему состоянию.
6. Reducer `operator.add` сохраняет историю сообщений.
7. Ребро `tool → llm` создаёт ReAct-цикл.
8. `START` определяет точку входа, а `END` — завершение workflow.
9. `max_steps` ограничивает вызовы LLM, а `recursion_limit` страхует граф.
10. LangGraph меняет способ оркестрации, но позволяет переиспользовать
    промпт, протокол и инструменты первого урока.

## Задания для самостоятельной практики

1. Добавьте Node `validate_question`, который отклоняет пустые или слишком
   длинные вопросы до вызова LLM.
2. Добавьте в State список всех выполненных `ToolCall`.
3. Создайте reducer для журнала Observation.
4. Добавьте отдельный лимит количества вызовов инструментов.
5. Запретите повторный вызов одного инструмента с одинаковыми аргументами.
6. Добавьте Node подтверждения перед потенциально опасным инструментом.
7. Напишите тест для маршрута `retry` после невалидного JSON.
8. Напишите тест для маршрута `step_limit`.
9. Добавьте checkpoint и запустите два шага с одним `thread_id`.
10. Реализуйте потоковый вывод событий графа.
11. Замените ручной JSON-протокол нативным tool calling.
12. Добавьте отдельный Node, который форматирует финальный ответ перед `END`.
