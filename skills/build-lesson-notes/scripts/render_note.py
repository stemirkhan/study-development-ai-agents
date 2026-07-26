#!/usr/bin/env python3
"""Render a validated Markdown lesson note to PDF with Chromium."""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import markdown
except ImportError:  # pragma: no cover - exercised only outside the project env.
    markdown = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a lesson note to PDF.")
    parser.add_argument("note", type=Path, help="Path to the Markdown note")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PDF; defaults to NOTE with a .pdf suffix",
    )
    return parser.parse_args()


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed with ---")

    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if line.startswith("  "):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values, text[end + 5 :]


def find_chromium() -> str:
    configured = os.environ.get("CHROME_BIN", "").strip()
    candidates = [
        configured,
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError(
        "Chromium/Chrome not found; install it or set CHROME_BIN"
    )


def build_html(title: str, body: str, css: str, base_href: str) -> str:
    if markdown is None:
        raise RuntimeError(
            "Python-Markdown is not installed; run `uv sync` and retry"
        )
    rendered = markdown.markdown(
        body,
        extensions=["extra", "sane_lists", "toc"],
        output_format="html5",
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <base href="{html.escape(base_href, quote=True)}">
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
{rendered}
</body>
</html>
"""


def validate_note(note: Path) -> None:
    validator = Path(__file__).resolve().parent / "validate_note.py"
    result = subprocess.run(
        [sys.executable, str(validator), str(note)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        details = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise RuntimeError(f"note validation failed:\n{details}")


def main() -> int:
    args = parse_args()
    note = args.note.expanduser().resolve()
    output = (
        args.output.expanduser().resolve()
        if args.output
        else note.with_suffix(".pdf")
    )
    css_path = Path(__file__).resolve().parent.parent / "assets" / "note.css"

    if note.suffix.lower() != ".md":
        print("error: input note must have a .md suffix", file=sys.stderr)
        return 2
    if output.suffix.lower() != ".pdf":
        print("error: output must have a .pdf suffix", file=sys.stderr)
        return 2
    if output == note:
        print("error: output must differ from the source note", file=sys.stderr)
        return 2

    try:
        validate_note(note)
        text = note.read_text(encoding="utf-8")
        metadata, body = split_frontmatter(text)
        css = css_path.read_text(encoding="utf-8")
        base_href = f"{note.parent.as_uri().rstrip('/')}/"
        document = build_html(
            metadata.get("title", note.stem),
            body,
            css,
            base_href,
        )
        chromium = find_chromium()
        output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=".lesson-note-",
            dir=output.parent,
        ) as temp_dir:
            temp = Path(temp_dir)
            html_path = temp / "note.html"
            profile = temp / "chrome-profile"
            html_path.write_text(document, encoding="utf-8", newline="\n")
            command = [
                chromium,
                "--headless",
                "--disable-gpu",
                "--disable-background-networking",
                "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={output}",
                html_path.as_uri(),
            ]
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                details = (result.stderr or result.stdout).strip()
                raise RuntimeError(
                    f"Chromium exited with {result.returncode}: {details}"
                )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not output.is_file() or output.stat().st_size < 1000:
        print(f"error: Chromium did not create a valid PDF: {output}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
