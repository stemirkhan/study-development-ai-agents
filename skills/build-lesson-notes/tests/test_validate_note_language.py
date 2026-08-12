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
from validate_note import parse_frontmatter, split_frontmatter, validate


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
Маршрутизатор возвращает `NoCompatibleRoute`, а затем агент обрабатывает
`ResponseCompleted`. Названия OpenAI Responses, Structured Outputs,
OpenAI Realtime и JSON Schema сохранены точно.
[Документация](https://example.com/fallback-route/streaming)
Точные имена `LLM Agent tool tool call prompt context model response
UI prefix history assistant message terminal response
committed provisional cancellation cleanup runtime adapter source consumer chunk
attempt intent timeout backpressure` остаются кодом.

```text
LLM Agent tool tool call prompt context model response fallback route latency
side effect UI prefix history assistant message
terminal response committed provisional cancellation cleanup runtime adapter
source consumer chunk attempt intent timeout backpressure
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


def test_language_guard_covers_common_explanatory_anglicisms() -> None:
    expected = {
        "LLM": "языковая модель",
        "Agent": "агент",
        "tool call": "вызов инструмента",
        "tool": "инструмент",
        "prompt": "инструкция или запрос",
        "context": "контекст",
        "model": "модель",
        "response": "ответ",
        "SDK": "набор разработчика",
        "UI": "интерфейс",
        "prefix": "начальный фрагмент",
        "history": "история",
        "assistant message": "сообщение ассистента",
        "terminal response": "завершённый ответ",
        "committed": "подтверждённый",
        "provisional": "предварительный",
        "cancellation": "отмена",
        "cleanup": "освобождение ресурсов",
        "runtime": "среда выполнения",
        "adapter": "адаптер",
        "source": "источник",
        "consumer": "потребитель",
        "chunk": "фрагмент",
        "attempt": "попытка",
        "intent": "намерение",
        "timeout": "истечение времени ожидания",
        "backpressure": "обратное давление",
    }
    text = note_text(
        status="complete",
        content=" ".join(expected),
    )

    findings = validate(text)

    message = "\n".join(findings.errors)
    for term, replacement in expected.items():
        assert f"{term} → «{replacement}»" in message


def test_complete_note_rejects_unknown_latin_prose() -> None:
    text = note_text(
        status="complete",
        content="Pipeline обрабатывает payload.",
    )

    findings = validate(text)

    message = "\n".join(findings.errors)
    assert "неразрешённая латиница → payload, Pipeline" in message


def test_rejects_the_mixed_streaming_sentence_regression() -> None:
    text = note_text(
        status="complete",
        content=(
            "UI может показать незавершённый prefix, но history Agent не "
            "получает обычный assistant message. Если terminal response имеет "
            "особый исход, state сохраняет его."
        ),
    )

    findings = validate(text)

    message = "\n".join(findings.errors)
    for term in (
        "UI",
        "prefix",
        "history",
        "Agent",
        "assistant message",
        "terminal response",
        "state",
    ):
        assert f"{term} →" in message


def test_known_products_standards_and_identifiers_are_allowed() -> None:
    text = note_text(
        status="complete",
        content="""
OpenAI Responses, OpenAI Realtime, OpenAI Models API, Anthropic Messages API,
Anthropic Models API, Gemini Models API, Gemini API, Google Gemini,
Interactions API, JSON Schema, Python, HTTP, SSE, RFC и `inline_name`.
""",
    )

    findings = validate(text)

    assert findings.errors == []
    assert findings.warnings == []


def test_all_complete_lesson_notes_pass_validation() -> None:
    repository = Path(__file__).resolve().parents[3]
    notes = sorted(repository.glob("notes/module_*/*.md"))
    complete_notes: list[Path] = []
    failures: list[str] = []

    for path in notes:
        text = path.read_text(encoding="utf-8")
        frontmatter, _ = split_frontmatter(text)
        values, _ = parse_frontmatter(frontmatter)
        if values.get("status") != "complete":
            continue
        complete_notes.append(path)
        findings = validate(text, path)
        if findings.errors:
            relative = path.relative_to(repository)
            details = "\n".join(f"    - {error}" for error in findings.errors)
            failures.append(f"  {relative}:\n{details}")

    assert complete_notes, "не найдено ни одного завершённого конспекта"
    assert not failures, (
        "завершённые конспекты не прошли validate_note.py:\n"
        + "\n".join(failures)
    )
