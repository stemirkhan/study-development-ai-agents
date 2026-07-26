#!/usr/bin/env python3
"""Create an editable lesson diagram HTML source from the bundled template."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

from diagram_support import DiagramError, validate_diagram_title


PLACEHOLDERS = {
    "{{TITLE}}",
    "{{TITLE_JSON}}",
    "{{STYLE_HREF}}",
    "{{RUNTIME_HREF}}",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create diagrams/NAME.html next to a lesson note."
    )
    parser.add_argument("note", type=Path, help="Path to the lesson Markdown note")
    parser.add_argument("--name", required=True, help="ASCII diagram name")
    parser.add_argument("--title", required=True, help="Accessible diagram title")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing HTML source",
    )
    return parser.parse_args()


def normalize_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    if not name:
        raise ValueError("name must contain ASCII letters or digits")
    return name


def render_template(
    template: str,
    *,
    title: str,
    style_href: str,
    runtime_href: str,
) -> str:
    missing = sorted(token for token in PLACEHOLDERS if token not in template)
    if missing:
        raise ValueError(f"template is missing placeholders: {', '.join(missing)}")
    title_json = json.dumps(title, ensure_ascii=False)
    title_json = (
        title_json.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    values = {
        "{{TITLE}}": html.escape(title),
        "{{TITLE_JSON}}": title_json,
        "{{STYLE_HREF}}": html.escape(style_href, quote=True),
        "{{RUNTIME_HREF}}": html.escape(runtime_href, quote=True),
    }
    pattern = re.compile(
        "|".join(
            re.escape(token)
            for token in sorted(PLACEHOLDERS, key=len, reverse=True)
        )
    )
    return pattern.sub(lambda match: values[match.group(0)], template)


def main() -> int:
    args = parse_args()
    note = args.note.expanduser().resolve()
    title = args.title.strip()
    if note.suffix.lower() != ".md" or not note.is_file():
        print(f"error: note must be an existing .md file: {note}", file=sys.stderr)
        return 2
    try:
        title = validate_diagram_title(title)
    except DiagramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        name = normalize_name(args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    skill_root = Path(__file__).resolve().parent.parent
    template_path = skill_root / "assets" / "diagram-template.html"
    diagrams_dir = note.parent / "diagrams"
    if diagrams_dir.is_symlink():
        print(
            f"error: refusing to use a symlinked diagrams directory: {diagrams_dir}",
            file=sys.stderr,
        )
        return 1
    if diagrams_dir.exists() and not diagrams_dir.is_dir():
        print(
            f"error: diagrams path is not a directory: {diagrams_dir}",
            file=sys.stderr,
        )
        return 1
    try:
        diagrams_dir.mkdir(exist_ok=True)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if diagrams_dir.resolve().parent != note.parent:
        print(
            f"error: diagrams directory escapes the note directory: {diagrams_dir}",
            file=sys.stderr,
        )
        return 1

    output = diagrams_dir / f"{name}.html"
    if output.is_symlink():
        print(f"error: refusing to overwrite a symlink: {output}", file=sys.stderr)
        return 1
    if output.exists() and not args.force:
        print(
            f"error: diagram already exists: {output} (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1
    if output.exists() and not output.is_file():
        print(f"error: output is not a regular file: {output}", file=sys.stderr)
        return 1

    try:
        template = template_path.read_text(encoding="utf-8")
        style_href = Path(
            os.path.relpath(skill_root / "assets" / "diagram.css", output.parent)
        ).as_posix()
        runtime_href = Path(
            os.path.relpath(skill_root / "assets" / "diagram.js", output.parent)
        ).as_posix()
        rendered = render_template(
            template,
            title=title,
            style_href=style_href,
            runtime_href=runtime_href,
        )
        mode = "w" if args.force else "x"
        with output.open(mode, encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
