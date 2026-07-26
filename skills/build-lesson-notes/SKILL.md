---
name: build-lesson-notes
description: Создавать, обновлять и проверять единообразные учебные конспекты уроков в Markdown и производном PDF для Obsidian. Использовать при просьбах написать конспект модуля или урока, превратить учебные материалы в заметку, обновить существующий конспект, проверить его структуру либо пересобрать PDF.
---

# Конспекты уроков

Создавать один содержательный Markdown-файл и генерировать из него PDF.
Хранить оба файла рядом в `notes/module_XX_topic/`.

## Обязательные принципы

- Считать Markdown единственным source of truth. Не редактировать PDF вручную.
- Работать только с текущим модулем из
  `docs/senior_ai_agent_engineer_2026.md`.
- Писать для Senior Python Developer с DevOps-опытом: не пересказывать основы.
- Выделять ключевые мысли своими словами, а не копировать урок целиком.
- Объяснять сложное простым языком, не теряя технической точности.
- Добавлять только полезные примеры, команды и небольшие фрагменты кода.
- Использовать обычный Markdown вместо Obsidian-only wikilinks и embeds, чтобы
  Markdown и PDF оставались эквивалентны.
- Для изменчивых API указывать источник, проверенную версию и дату.

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
6. На полный код и дополнительные материалы ссылаться относительным
   Markdown-link.

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
Открывать полученный PDF и визуально проверять заголовки, таблицы, code blocks,
переносы страниц и отсутствие обрезанного текста.

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
- `scripts/create_note.py` — безопасное создание нового source-файла.
- `scripts/validate_note.py` — структурная и portability-проверка.
- `scripts/render_note.py` — локальная генерация PDF через Chromium.
