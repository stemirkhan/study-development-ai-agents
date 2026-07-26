#!/usr/bin/env python3
"""Create a lesson note from the bundled Markdown template."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


PLACEHOLDERS = {
    "{{TITLE_YAML}}",
    "{{MODULE_NUMBER}}",
    "{{TOPIC}}",
    "{{UPDATED}}",
    "{{TITLE}}",
    "{{PLAN_LINK}}",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a module note in notes/module_XX_topic/."
    )
    parser.add_argument("--module", required=True, help="Module number, 1-99")
    parser.add_argument(
        "--topic",
        required=True,
        help="Short ASCII topic name, for example context-engineering",
    )
    parser.add_argument("--title", required=True, help="Human-readable note title")
    parser.add_argument(
        "--notes-root",
        type=Path,
        help="Notes directory; defaults to <repository>/notes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the note if it already exists",
    )
    return parser.parse_args()


def normalize_module(raw: str) -> str:
    if not re.fullmatch(r"\d{1,2}", raw):
        raise ValueError("module must be an integer from 1 to 99")
    value = int(raw)
    if value < 1:
        raise ValueError("module must be an integer from 1 to 99")
    return f"{value:02d}"


def normalize_topic(raw: str) -> str:
    topic = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    if not topic:
        raise ValueError("topic must contain ASCII letters or digits")
    return topic


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def render_template(
    template: str,
    *,
    title: str,
    module: str,
    topic: str,
    updated: str,
) -> str:
    missing = sorted(token for token in PLACEHOLDERS if token not in template)
    if missing:
        raise ValueError(f"template is missing placeholders: {', '.join(missing)}")

    values = {
        "{{TITLE_YAML}}": json.dumps(title, ensure_ascii=False),
        "{{MODULE_NUMBER}}": module,
        "{{TOPIC}}": topic,
        "{{UPDATED}}": updated,
        "{{TITLE}}": title,
        "{{PLAN_LINK}}": "../../docs/senior_ai_agent_engineer_2026.md",
    }
    rendered = template
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    return rendered


def main() -> int:
    args = parse_args()
    title = args.title.strip()
    if not title:
        print("error: title must not be empty", file=sys.stderr)
        return 2
    try:
        module = normalize_module(args.module)
        topic = normalize_topic(args.topic)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    root = repository_root()
    notes_root = (
        args.notes_root.expanduser().resolve()
        if args.notes_root
        else root / "notes"
    )
    stem = f"module_{module}_{topic}"
    output = notes_root / stem / f"{stem}.md"
    template_path = (
        Path(__file__).resolve().parent.parent / "assets" / "note-template.md"
    )

    if output.exists() and not args.force:
        print(
            f"error: note already exists: {output} (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1
    if output.exists() and not output.is_file():
        print(f"error: output is not a regular file: {output}", file=sys.stderr)
        return 1

    try:
        template = template_path.read_text(encoding="utf-8")
        rendered = render_template(
            template,
            title=title,
            module=module,
            topic=topic,
            updated=date.today().isoformat(),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if args.force else "x"
        with output.open(mode, encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
