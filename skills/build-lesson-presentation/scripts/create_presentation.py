#!/usr/bin/env python3
"""Create a self-contained lesson presentation from the bundled template."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path


PLACEHOLDERS = {
    "title": "{{PRESENTATION_TITLE}}",
    "subtitle": "{{PRESENTATION_SUBTITLE}}",
    "module": "{{MODULE_LABEL}}",
    "description": "{{PRESENTATION_DESCRIPTION}}",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an HTML lesson presentation from the bundled template."
    )
    parser.add_argument("output", type=Path, help="Path for the generated HTML file")
    parser.add_argument("--title", required=True, help="Presentation title")
    parser.add_argument("--subtitle", required=True, help="Presentation subtitle")
    parser.add_argument("--module", required=True, help="Module name or number")
    parser.add_argument(
        "--description",
        required=True,
        help="Short presentation description",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite OUTPUT if it already exists",
    )
    return parser.parse_args()


def render(template: str, values: dict[str, str]) -> str:
    rendered = template
    missing = [
        token for key, token in PLACEHOLDERS.items() if token not in rendered
    ]
    if missing:
        formatted = ", ".join(missing)
        raise ValueError(f"template is missing placeholders: {formatted}")

    escaped_by_token = {
        token: html.escape(values[key], quote=True)
        for key, token in PLACEHOLDERS.items()
    }
    pattern = re.compile("|".join(re.escape(token) for token in PLACEHOLDERS.values()))
    return pattern.sub(lambda match: escaped_by_token[match.group(0)], rendered)


def main() -> int:
    args = parse_args()
    template_path = (
        Path(__file__).resolve().parent.parent
        / "assets"
        / "presentation-template.html"
    )
    output_path = args.output.expanduser().resolve()

    if not template_path.is_file():
        print(f"error: template not found: {template_path}", file=sys.stderr)
        return 1
    if output_path.exists() and not args.force:
        print(
            f"error: output already exists: {output_path} "
            "(pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1
    if output_path.exists() and not output_path.is_file():
        print(f"error: output is not a regular file: {output_path}", file=sys.stderr)
        return 1

    try:
        template = template_path.read_text(encoding="utf-8")
        rendered = render(
            template,
            {
                "title": args.title,
                "subtitle": args.subtitle,
                "module": args.module,
                "description": args.description,
            },
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if args.force else "x"
        with output_path.open(mode, encoding="utf-8", newline="\n") as output:
            output.write(rendered)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
