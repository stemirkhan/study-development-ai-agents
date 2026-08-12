---
name: build-lesson-notes
description: Создавать, обновлять и проверять единообразные учебные конспекты уроков в Markdown и производном PDF для Obsidian, включая локальные учебные схемы из HTML в PNG. Использовать при просьбах написать конспект модуля или урока, превратить учебные материалы в заметку, добавить поясняющую архитектурную/flow/sequence-схему, обновить существующий конспект, проверить его структуру либо пересобрать PDF.
---

# Конспекты уроков

Создавать один содержательный Markdown-файл и генерировать из него PDF.
Хранить оба файла рядом в `notes/module_XX_topic/`.

## Обязательные принципы

- Считать Markdown source of truth для текста, порядка и ссылок конспекта.
  Для каждой схемы считать `diagrams/<name>.html` её source of truth, а PNG
  и PDF — derived artifacts. Не редактировать PNG или PDF вручную.
- Работать только с текущим модулем из
  `docs/senior_ai_agent_engineer_2026.md`.
- Писать для Senior Python Developer с DevOps-опытом: не пересказывать основы.
- Выделять ключевые мысли своими словами, а не копировать урок целиком.
- Объяснять сложное простым языком, не теряя технической точности.
- Добавлять только полезные примеры, команды и небольшие фрагменты кода.
- Использовать обычный Markdown вместо Obsidian-only wikilinks и embeds, чтобы
  Markdown и PDF оставались эквивалентны.
- Для изменчивых API указывать источник, проверенную версию и дату.
- **Язык конспекта**: писать связный текст, заголовки, таблицы, альтернативный
  текст изображений и подписи схем на естественном русском языке. Английскими
  оставлять только фундаментальные термины ИИ из основного плана (`LLM`,
  `Agent`, `tool`, `tool call`, `prompt`, `context`, `RAG`), точные
  идентификаторы кода и значения протокола, названия API, стандартов и
  продуктов. Если есть ясный русский эквивалент — «маршрут», «требование»,
  «крайний срок», «побочный эффект», «поток выполнения» — использовать его.
  При необходимости после русского термина указывать точное имя в обратных
  кавычках: «ошибка отсутствия совместимого маршрута
  (`NoCompatibleRoute`)». Не подменять объяснение цепочкой английских слов.

## Создание конспекта

1. Прочитать секцию текущего модуля в основном плане.
2. Прочитать README, код, тесты и артефакты урока, если они существуют.
3. При необходимости актуального поиска использовать `tavily-search` и
   проверять выводы по первичным источникам.
4. Создать файл из bundled template:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/create_note.py \
  --module 03 \
  --topic context-engineering \
  --title "Context Engineering"
```

Команда создаёт:

```text
notes/module_03_context_engineering/
└── module_03_context_engineering.md
```

5. Заполнить все разделы. Оставлять `status: draft`, пока текст не проверен.
6. Проверить, упростит ли схема понимание связи как минимум трёх компонентов,
   шагов или ветвей. Если да, создать 1–3 схемы по процедуре ниже; не добавлять
   их как декор.
7. На полный код и дополнительные материалы ссылаться относительным
   Markdown-link.
8. Перед `status: complete` отдельно вычитать заголовки, таблицы,
   альтернативный текст изображений и подписи схем: в них действует то же
   правило естественного русского языка, что и в основном тексте.

## Схемы HTML → PNG

При необходимости схемы полностью прочитать
`references/lesson-diagrams.md`. Создать source рядом с конспектом:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/create_diagram.py \
  notes/module_01_model_vs_agent/module_01_model_vs_agent.md \
  --name agent-runtime \
  --title "Agent runtime separates model calls from tool execution"
```

Изменять только JSON в `script#diagram-data`, затем сгенерировать PNG:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/render_diagram.py \
  notes/module_01_model_vs_agent/diagrams/agent-runtime.html
```

Вставить PNG обычным Markdown image с содержательным alt text:

```markdown
![Agent loop вызывает Model Client и Tool Executor, сохраняя state снаружи model](diagrams/agent-runtime.png)
```

HTML использует общую локальную тему skill и не обращается к сети. Renderer
встраивает в PNG fingerprints HTML, CSS, layout/runtime и pixel data.
Не копировать PNG без одноимённого HTML; stale, cropped или вручную изменённая
схема должна провалить validation.

## Проверка и PDF

Проверить формат:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/validate_note.py \
  notes/module_03_context_engineering/module_03_context_engineering.md
```

Сгенерировать PDF рядом с Markdown:

```bash
uv run --frozen python \
  skills/build-lesson-notes/scripts/render_note.py \
  notes/module_03_context_engineering/module_03_context_engineering.md
```

После каждого содержательного изменения повторять validation и PDF render.
Перед PDF открыть каждый новый PNG и проверить направление стрелок, подписи,
отсутствие пересечений и читаемость. Затем открыть PDF и визуально проверить
схемы, заголовки, таблицы, code blocks, переносы страниц и отсутствие
обрезанного текста.

## Обновление существующего конспекта

- Не создавать второй source-файл для того же модуля без явной необходимости.
- Сохранять полезные авторские выводы и исправлять только устаревшее.
- Обновлять поле `updated`.
- При изменении API обновлять source version/date и связанные примеры.
- Менять `status` на `complete` после проверки содержания и ссылок.
- Всегда пересобирать PDF; stale PDF считается ошибкой.

## Ресурсы

- `assets/note-template.md` — обязательная структура Markdown.
- `assets/note.css` — единый печатный стиль.
- `assets/diagram-template.html` — редактируемый HTML template схемы.
- `assets/diagram.css`, `assets/diagram.js` — единый visual style и layout.
- `references/lesson-diagrams.md` — JSON contract и правила композиции.
- `scripts/create_note.py` — безопасное создание нового source-файла.
- `scripts/create_diagram.py` — создание HTML source рядом с конспектом.
- `scripts/render_diagram.py` — проверка и Chromium render HTML в PNG.
- `scripts/validate_note.py` — структурная и portability-проверка.
- `scripts/render_note.py` — локальная генерация PDF через Chromium.
