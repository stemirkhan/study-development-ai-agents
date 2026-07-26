#!/usr/bin/env python3
"""Validate the structure and portability of a lesson note."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


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


def validate(text: str) -> Findings:
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
    if re.search(r"!\[[^\]]*\]\(\s*https?://", body, flags=re.IGNORECASE):
        findings.error("remote images are not reproducible; store images locally")

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

    findings = validate(text)
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
