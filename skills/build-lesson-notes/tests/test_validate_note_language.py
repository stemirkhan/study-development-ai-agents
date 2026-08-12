from __future__ import annotations

from pathlib import Path

from diagram_support import (
    FINGERPRINT_KEY,
    PIXEL_FINGERPRINT_KEY,
    SCALE_KEY,
    TEMPLATE_VERSION,
    VERSION_KEY,
    add_png_text,
    diagram_fingerprint,
    load_diagram,
    png_pixel_fingerprint,
)
from test_diagram_support import tiny_png, write_diagram
from validate_note import validate


def note_text(*, status: str, content: str) -> str:
    return f"""---
title: "Проверка языка"
module: "01"
topic: "language_test"
status: {status}
updated: "2026-08-12"
tags:
  - ai-agent-engineering
  - module-01
---

# Проверка языка

## Краткое содержание

Короткое содержание.

## Ключевые понятия

Ключевые понятия.

## Основные идеи

{content}

## Примеры

Небольшой пример.

## Заметки и наблюдения

Наблюдения.

## Вопросы для повторения

Что проверяет правило?

## Итоги

Краткий итог.

## Источники

[Источник](https://example.com)
"""


def test_complete_note_rejects_discouraged_english_prose() -> None:
    text = note_text(
        status="complete",
        content="Router выбирает fallback route по latency.",
    )

    findings = validate(text)

    message = "\n".join(findings.errors)
    assert "англицизмы в связном тексте" in message
    assert "fallback → «резервный маршрут или переключение»" in message
    assert "route → «маршрут»" in message
    assert "latency → «задержка»" in message


def test_draft_note_warns_about_discouraged_english_prose() -> None:
    text = note_text(
        status="draft",
        content="Router выбирает fallback route.",
    )

    findings = validate(text)

    assert findings.errors == []
    assert any(
        "англицизмы в связном тексте" in item for item in findings.warnings
    )


def test_exact_identifiers_products_and_urls_are_allowed() -> None:
    text = note_text(
        status="complete",
        content="""
Маршрутизатор возвращает `NoCompatibleRoute`, а затем Agent обрабатывает
`ResponseCompleted`. Названия OpenAI Responses, Structured Outputs,
OpenAI Realtime и JSON Schema сохранены точно.
[Документация](https://example.com/fallback-route/streaming)

```text
fallback route latency side effect
```
""",
    )

    findings = validate(text)

    assert findings.errors == []
    assert findings.warnings == []


def test_language_guard_checks_heading_table_and_image_alt() -> None:
    text = note_text(
        status="complete",
        content="""
### Execution flow

| Route | Result |
|---|---|
| Основной | compatible |

![Fallback route](diagram.png)
""",
    )

    findings = validate(text)

    message = "\n".join(findings.errors)
    assert "execution flow → «поток выполнения»" in message
    assert "route → «маршрут»" in message
    assert "fallback → «резервный маршрут или переключение»" in message


def test_language_guard_checks_diagram_labels(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    data = {
        "title": "Проверка схемы",
        "width": 720,
        "height": 480,
        "direction": "top-down",
        "nodes": [
            {
                "id": "agent",
                "label": "Agent",
                "rank": 0,
                "order": 0,
                "kind": "actor",
            },
            {
                "id": "fallback",
                "label": "Fallback route",
                "rank": 1,
                "order": 0,
                "kind": "accent",
            },
        ],
        "edges": [{"from": "agent", "to": "fallback", "label": "выбор"}],
    }
    source = write_diagram(tmp_path / "diagrams", data=data)
    document = load_diagram(source)
    output = source.with_suffix(".png")
    png = tiny_png(1440, 960)
    output.write_bytes(
        add_png_text(
            png,
            {
                FINGERPRINT_KEY: diagram_fingerprint(document),
                PIXEL_FINGERPRINT_KEY: png_pixel_fingerprint(png),
                SCALE_KEY: "2",
                VERSION_KEY: TEMPLATE_VERSION,
            },
        )
    )
    text = note_text(
        status="complete",
        content="![Порядок выбора маршрута](diagrams/runtime.png)",
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any(
        "англицизмы в подписях схемы" in item
        and "fallback → «резервный маршрут или переключение»" in item
        for item in findings.errors
    )
