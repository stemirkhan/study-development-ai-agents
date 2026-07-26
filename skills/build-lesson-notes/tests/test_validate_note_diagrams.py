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


def note_text(image: str) -> str:
    return f"""---
title: "Diagram test"
module: "01"
topic: "diagram_test"
status: draft
updated: "2026-07-26"
tags:
  - ai-agent-engineering
  - module-01
---

# Diagram test

## Краткое содержание

Короткое содержание.

## Ключевые понятия

Понятия.

## Основные идеи

{image}

## Примеры

Пример.

## Заметки и наблюдения

Наблюдения.

## Вопросы для повторения

Вопрос?

## Итоги

Итог.

## Источники

[Source](https://example.com)
"""


def test_validate_note_accepts_current_html_png_pair(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    source = write_diagram(tmp_path / "diagrams")
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
        "![Agent loop receives a prompt from User](diagrams/runtime.png)"
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert findings.errors == []


def test_validate_note_rejects_stale_diagram(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    source = write_diagram(tmp_path / "diagrams")
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
    source.write_text(
        source.read_text(encoding="utf-8") + "\n<!-- changed -->\n",
        encoding="utf-8",
    )
    text = note_text(
        "![Agent loop receives a prompt from User](diagrams/runtime.png)"
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any("PNG is stale" in error for error in findings.errors)


def test_validate_note_rejects_empty_alt_and_path_escape(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(tiny_png())
    text = note_text("![](../outside.png)")
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert "Markdown images must have descriptive alt text" in findings.errors
    assert any("escapes the note directory" in error for error in findings.errors)


def test_validate_note_rejects_empty_image_target(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    text = note_text("![Architecture diagram]()")
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert "Markdown image target must not be empty" in findings.errors


def test_validate_note_rejects_control_character_in_image_path(
    tmp_path: Path,
) -> None:
    note = tmp_path / "module.md"
    text = note_text("![Architecture diagram](diagrams/%00runtime.png)")
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any(
        "image path contains control characters" in error
        for error in findings.errors
    )


def test_validate_note_rejects_remote_reference_image(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    text = note_text(
        "![Remote architecture][arch]\n\n"
        "[arch]: https://attacker.invalid/diagram.png"
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any("remote images" in error for error in findings.errors)


def test_validate_note_rejects_escaped_alt_remote_image(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    text = note_text(
        r"![Architecture \] flow](https://attacker.invalid/diagram.png)"
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any("remote images" in error for error in findings.errors)


def test_validate_note_rejects_reference_path_escape(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(tiny_png())
    text = note_text(
        "![Architecture][arch]\n\n"
        "[arch]: ../outside.png"
    )
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any("escapes the note directory" in error for error in findings.errors)


def test_validate_note_rejects_symlinked_diagram_png(tmp_path: Path) -> None:
    note = tmp_path / "module.md"
    diagrams = tmp_path / "diagrams"
    assets = tmp_path / "assets"
    diagrams.mkdir()
    assets.mkdir()
    manual = assets / "manual.png"
    manual.write_bytes(tiny_png())
    (diagrams / "runtime.png").symlink_to(manual)
    text = note_text("![Agent runtime](diagrams/runtime.png)")
    note.write_text(text, encoding="utf-8")

    findings = validate(text, note)

    assert any("must not contain symlinks" in error for error in findings.errors)
