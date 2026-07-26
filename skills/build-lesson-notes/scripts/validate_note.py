#!/usr/bin/env python3
"""Validate the structure and portability of a lesson note."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from diagram_support import DiagramError, validate_rendered_png

try:
    import markdown
except ImportError:  # pragma: no cover - exercised only outside the project env.
    markdown = None


REQUIRED_FIELDS = {"title", "module", "topic", "status", "updated", "tags"}
REQUIRED_SECTIONS = [
    "Краткое содержание",
    "Ключевые понятия",
    "Основные идеи",
    "Примеры",
    "Заметки и наблюдения",
    "Вопросы для повторения",
    "Итоги",
    "Источники",
]
VALID_STATUSES = {"draft", "reviewed", "complete"}
RAW_HTML = re.compile(
    r"<\s*/?\s*[a-z][a-z0-9-]*(?:\s+[^>]*)?\s*/?>",
    flags=re.IGNORECASE | re.DOTALL,
)
ALLOWED_IMAGE_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".webp"}


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a lesson Markdown note.")
    parser.add_argument("note", type=Path, help="Path to the Markdown note")
    return parser.parse_args()


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed with ---")
    return text[4:end], text[end + 5 :]


def parse_frontmatter(frontmatter: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    tags: list[str] = []
    current_list = ""
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list:
            if current_list == "tags":
                tags.append(line[4:].strip().strip("\"'"))
            continue
        match = re.fullmatch(r"([a-z_]+):(?:\s*(.*))?", line)
        if not match:
            continue
        key, raw_value = match.groups()
        value = (raw_value or "").strip().strip("\"'")
        values[key] = value
        current_list = key if not value else ""
    return values, tags


def section_bodies(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1)] = body[start:end]
    return sections


def visible_text(section: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL)
    return without_comments.strip()


def prose_without_code(body: str) -> str:
    without_fences = re.sub(
        r"^(```|~~~).*?^\1\s*$",
        "",
        body,
        flags=re.MULTILINE | re.DOTALL,
    )
    without_inline = re.sub(r"`[^`\n]+`", "", without_fences)
    return re.sub(r"<!--.*?-->", "", without_inline, flags=re.DOTALL)


class _ImageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "img":
            return
        values = {name: value or "" for name, value in attrs}
        self.images.append((values.get("alt", ""), values.get("src", "")))


def markdown_images(body: str) -> list[tuple[str, str]]:
    if markdown is None:
        raise RuntimeError(
            "Python-Markdown is not installed; run `uv sync` and retry"
        )
    rendered = markdown.markdown(
        body,
        extensions=["extra", "sane_lists", "toc"],
        output_format="html5",
    )
    parser = _ImageHTMLParser()
    parser.feed(rendered)
    parser.close()
    return parser.images


def validate_images(body: str, note_path: Path | None, findings: Findings) -> None:
    try:
        images = markdown_images(body)
    except RuntimeError as exc:
        findings.error(str(exc))
        return
    for alt_text, raw_target in images:
        alt_text = alt_text.strip()
        raw_target = raw_target.strip()
        if not alt_text:
            findings.error("Markdown images must have descriptive alt text")
        if not raw_target:
            findings.error("Markdown image target must not be empty")
            continue
        parsed = urlsplit(raw_target)
        if parsed.scheme in {"http", "https"}:
            findings.error(
                "remote images are not reproducible; store images locally"
            )
            continue
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            findings.error(f"image must use a plain local path: {raw_target}")
            continue

        decoded = unquote(parsed.path)
        if any(
            ord(character) < 32 or ord(character) == 127
            for character in decoded
        ):
            findings.error(
                f"image path contains control characters: {raw_target}"
            )
            continue
        relative = Path(decoded)
        if relative.is_absolute() or "\\" in decoded:
            findings.error(f"image path must be local and relative: {raw_target}")
            continue
        if note_path is None:
            continue

        note_dir = note_path.resolve().parent
        lexical_relative = Path(os.path.normpath(decoded))
        candidate = note_dir
        symlink_component: Path | None = None
        for part in lexical_relative.parts:
            candidate /= part
            if candidate.is_symlink():
                symlink_component = candidate
                break
        if symlink_component is not None:
            findings.error(
                f"image path must not contain symlinks: {raw_target}"
            )
            continue
        try:
            image_path = (note_dir / lexical_relative).resolve()
        except (OSError, RuntimeError, ValueError):
            findings.error(f"image path cannot be resolved: {raw_target}")
            continue
        if not image_path.is_relative_to(note_dir):
            findings.error(f"image path escapes the note directory: {raw_target}")
            continue
        if image_path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            findings.error(f"unsupported image type: {raw_target}")
            continue
        if not image_path.is_file():
            findings.error(f"local image does not exist: {raw_target}")
            continue

        relative_parts = lexical_relative.parts
        if (
            relative_parts
            and relative_parts[0] == "diagrams"
            and image_path.suffix.lower() == ".png"
        ):
            source = image_path.with_suffix(".html")
            if not source.is_file():
                findings.error(
                    f"diagram PNG has no same-name HTML source: {raw_target}"
                )
                continue
            try:
                validate_rendered_png(source, image_path)
            except DiagramError as exc:
                findings.error(f"invalid diagram {raw_target}: {exc}")


def validate(text: str, note_path: Path | None = None) -> Findings:
    findings = Findings()
    try:
        frontmatter, body = split_frontmatter(text)
    except ValueError as exc:
        findings.error(str(exc))
        return findings

    values, tags = parse_frontmatter(frontmatter)
    missing_fields = sorted(REQUIRED_FIELDS - values.keys())
    if missing_fields:
        findings.error(f"missing frontmatter fields: {', '.join(missing_fields)}")

    module = values.get("module", "")
    if module and not re.fullmatch(r"\d{2}", module):
        findings.error("frontmatter module must use two digits, for example 03")

    status = values.get("status", "")
    if status and status not in VALID_STATUSES:
        findings.error(
            f"frontmatter status must be one of: {', '.join(sorted(VALID_STATUSES))}"
        )

    updated = values.get("updated", "")
    if updated:
        try:
            date.fromisoformat(updated)
        except ValueError:
            findings.error("frontmatter updated must use YYYY-MM-DD")

    if values.get("title", "") == "":
        findings.error("frontmatter title must not be empty")
    if values.get("topic", "") == "":
        findings.error("frontmatter topic must not be empty")
    if "ai-agent-engineering" not in tags:
        findings.error("tags must include ai-agent-engineering")
    if module and f"module-{module}" not in tags:
        findings.error(f"tags must include module-{module}")

    if re.search(r"\{\{[A-Z0-9_]+\}\}", text):
        findings.error("unresolved template placeholders found")
    if re.search(r"!?\[\[[^\]]+\]\]", body):
        findings.error(
            "Obsidian-only wikilinks/embeds are not portable; use Markdown links"
        )
    if RAW_HTML.search(prose_without_code(body)):
        findings.error("raw HTML is not allowed in lesson notes")
    validate_images(body, note_path, findings)

    sections = section_bodies(body)
    missing_sections = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing_sections:
        findings.error(f"missing sections: {', '.join(missing_sections)}")

    for name in REQUIRED_SECTIONS:
        section = sections.get(name, "")
        if section and not visible_text(section):
            message = f"section is empty: {name}"
            if status == "complete":
                findings.error(message)
            else:
                findings.warning(message)

    sources = visible_text(sections.get("Источники", ""))
    if status == "complete" and not re.search(r"\[[^\]]+\]\([^)]+\)", sources):
        findings.error("complete note must contain Markdown links in Источники")

    return findings


def main() -> int:
    args = parse_args()
    path = args.note.expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = validate(text, path)
    for message in findings.errors:
        print(f"ERROR: {message}")
    for message in findings.warnings:
        print(f"WARNING: {message}")

    if findings.errors:
        print(
            f"FAILED: {len(findings.errors)} error(s), "
            f"{len(findings.warnings)} warning(s)"
        )
        return 1

    print(f"OK: {path} ({len(findings.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
