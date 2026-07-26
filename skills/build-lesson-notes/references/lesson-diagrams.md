# Учебные схемы

## Когда рисовать

Создавать схему, только если она быстрее текста объясняет хотя бы одно:

- связь трёх и более компонентов;
- иерархию, branching или ownership boundary;
- цикл либо последовательность из трёх и более шагов;
- изменение state между событиями.

Ограничивать конспект 1–3 схемами. Для одного факта, двух сущностей или
декоративной иллюстрации использовать текст.

## Артефакты и workflow

Хранить рядом:

```text
notes/module_XX_topic/
├── module_XX_topic.md
└── diagrams/
    ├── agent-runtime.html  # source of truth схемы
    └── agent-runtime.png   # derived, добавляется в Markdown
```

1. Создать HTML через `scripts/create_diagram.py`.
2. Изменить только JSON внутри `script#diagram-data`. Не менять HTML shell,
   подключённые CSS/JS и template marker.
3. Сгенерировать одноимённый PNG через `scripts/render_diagram.py`.
4. Открыть PNG и проверить его визуально.
5. Добавить `![содержательный alt text](diagrams/<name>.png)` в Markdown.
6. Запустить `validate_note.py`, затем `render_note.py` и проверить PDF.

Проверить актуальность PNG без render:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/render_diagram.py \
  notes/module_01_model_vs_agent/diagrams/agent-runtime.html \
  --check
```

Fingerprint включает HTML source, общий CSS, layout runtime и renderer.
Metadata также фиксирует scale, dimensions и pixel data. После изменения
любого source-компонента PNG требуется пересобрать; cropped/edited PNG не
проходит validation.

## JSON contract

Корневой объект:

| Поле | Значение |
|---|---|
| `title` | Accessible title, 1–120 characters; runtime переносит его в HTML title/heading |
| `width` | Canvas width, integer `480..1600` |
| `height` | Canvas height, integer `320..1200` |
| `direction` | `top-down` или `left-right` |
| `nodes` | От 1 до 24 nodes |
| `edges` | До 48 directed edges |

Node:

```json
{
  "id": "agent-loop",
  "label": "Agent Loop\norchestration",
  "rank": 1,
  "order": 0,
  "kind": "accent"
}
```

- `id`: lowercase ASCII id, не более 48 characters.
- `label`: 1–120 characters и не более четырёх строк.
- `rank`: слой вдоль `direction`, integer `0..12`.
- `order`: позиция внутри слоя, integer `0..24`; пара `rank/order` уникальна.
- `kind`: `default`, `actor`, `accent`, `info`, `success` или `danger`.
- Переносить label через `\n`; использовать не более четырёх строк.

Edge:

```json
{
  "from": "user",
  "to": "agent-loop",
  "label": "Prompt",
  "lane": -1,
  "dashed": false
}
```

- `from` и `to` ссылаются на существующие node ids.
- `label`, если задан: 1–64 characters и не более четырёх строк.
- `lane` от `-2` до `2` разводит параллельные/обратные стрелки.
- `dashed: true` обозначает optional, async или failure path только когда
  смысл явно указан рядом.

## Композиция

- Формулировать в схеме один тезис; title и окружающий текст должны его назвать.
- Использовать `actor` для внешнего человека/системы, `accent` для главного
  orchestration component, остальные tones — только для смысловых категорий.
- Строить основной flow сверху вниз. `left-right` выбирать для timeline,
  request pipeline или длинного цикла.
- Ставить связанные nodes в соседние ranks. Длинные edges чаще пересекают
  промежуточные nodes.
- Для request/response между одной парой nodes задавать разные `lane`, например
  `-1` и `1`.
- Разбивать dense graph на две схемы, если labels становятся мелкими или edges
  пересекают nodes.
- Не кодировать критический смысл одним цветом: сохранять labels и направление
  стрелок.

## Ограничения

Renderer предназначен для небольших architecture, hierarchy и flow diagrams.
Он не заменяет arbitrary graph layout, BPMN/UML editor и сложные sequence
frames. Не добавлять external scripts, remote fonts, images, inline event
handlers и произвольный executable JavaScript: validator отклоняет их.
