#!/usr/bin/env python3
"""Render a validated lesson diagram HTML source to an atomic PNG."""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from diagram_support import (
    FINGERPRINT_KEY,
    PIXEL_FINGERPRINT_KEY,
    SCALE_KEY,
    TEMPLATE_VERSION,
    VERSION_KEY,
    DiagramError,
    DiagramDocument,
    add_png_text,
    diagram_fingerprint,
    load_diagram,
    png_pixel_fingerprint,
    validate_rendered_png,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a lesson diagram HTML source to PNG."
    )
    parser.add_argument("diagram", type=Path, help="Path to diagram HTML")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PNG; defaults to DIAGRAM with a .png suffix",
    )
    parser.add_argument(
        "--scale",
        type=int,
        choices=(1, 2, 3),
        default=2,
        help="PNG device scale factor (default: 2)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that the existing PNG matches its HTML and theme",
    )
    return parser.parse_args()


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
    raise RuntimeError("Chromium/Chrome not found; install it or set CHROME_BIN")


def _chromium_base(chromium: str, profile: Path) -> list[str]:
    return [
        chromium,
        "--headless",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--host-resolver-rules=MAP * ~NOTFOUND",
        "--no-first-run",
        "--no-default-browser-check",
        "--allow-file-access-from-files",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        f"--user-data-dir={profile}",
    ]


def _run_chromium(command: list[str], *, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"Chromium exited with {result.returncode}: {details}"
        )
    return result


class _RenderStateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "main":
            return
        values = {name: value or "" for name, value in attrs}
        if values.get("id") == "lesson-diagram":
            self.roots.append(
                (
                    values.get("data-render-state", ""),
                    values.get("data-render-error", ""),
                )
            )


def require_render_ready(document_html: str) -> None:
    parser = _RenderStateParser()
    parser.feed(document_html)
    parser.close()
    if len(parser.roots) != 1:
        raise RuntimeError(
            "rendered DOM must contain exactly one lesson-diagram main"
        )
    state, error = parser.roots[0]
    if state != "ready":
        raise RuntimeError(error or f"diagram runtime reported state {state!r}")


def build_snapshot_html(document: DiagramDocument) -> str:
    data_json = json.dumps(document.data, ensure_ascii=False, indent=2)
    data_json = (
        data_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    title = html.escape(document.title)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="diagram.css">
</head>
<body>
  <main id="lesson-diagram" aria-labelledby="diagram-title"
        data-render-state="pending">
    <h1 id="diagram-title" class="visually-hidden">{title}</h1>
    <script id="diagram-data" type="application/json">
{data_json}
    </script>
  </main>
  <script src="diagram.js"></script>
</body>
</html>
"""


def render(source: Path, output: Path, *, scale: int) -> None:
    document = load_diagram(source)
    source_fingerprint = diagram_fingerprint(document)
    chromium = find_chromium()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".lesson-diagram-",
        dir=output.parent,
    ) as temp_dir:
        temp = Path(temp_dir)
        browser_source = temp / "diagram.html"
        browser_source.write_text(
            build_snapshot_html(document),
            encoding="utf-8",
            newline="\n",
        )
        (temp / "diagram.css").write_bytes(document.style_bytes)
        (temp / "diagram.js").write_bytes(document.runtime_bytes)
        dump_command = _chromium_base(chromium, temp / "profile-dump") + [
            "--virtual-time-budget=1200",
            "--dump-dom",
            browser_source.as_uri(),
        ]
        dump = _run_chromium(dump_command)
        require_render_ready(dump.stdout)

        raw_png = temp / "raw.png"
        screenshot_command = _chromium_base(
            chromium,
            temp / "profile-screenshot",
        ) + [
            "--virtual-time-budget=1200",
            f"--window-size={document.width},{document.height}",
            f"--force-device-scale-factor={scale}",
            f"--screenshot={raw_png}",
            browser_source.as_uri(),
        ]
        _run_chromium(screenshot_command)
        if not raw_png.is_file() or raw_png.stat().st_size < 1000:
            raise RuntimeError("Chromium did not create a valid PNG")

        current_fingerprint = diagram_fingerprint(load_diagram(document.source))
        if current_fingerprint != source_fingerprint:
            raise RuntimeError(
                "diagram HTML, CSS, runtime, or renderer changed during render; "
                "retry from the current source"
            )
        raw_png_data = raw_png.read_bytes()
        annotated = add_png_text(
            raw_png_data,
            {
                FINGERPRINT_KEY: source_fingerprint,
                PIXEL_FINGERPRINT_KEY: png_pixel_fingerprint(raw_png_data),
                VERSION_KEY: TEMPLATE_VERSION,
                SCALE_KEY: str(scale),
            },
        )
        staged = temp / "diagram.png"
        staged.write_bytes(annotated)
        validate_rendered_png(document.source, staged)
        os.replace(staged, output)


def main() -> int:
    args = parse_args()
    source = args.diagram.expanduser().resolve()
    output = (
        args.output.expanduser().resolve()
        if args.output
        else source.with_suffix(".png")
    )
    if source.suffix.lower() != ".html":
        print("error: input diagram must have an .html suffix", file=sys.stderr)
        return 2
    if output.suffix.lower() != ".png":
        print("error: output must have a .png suffix", file=sys.stderr)
        return 2
    if source == output:
        print("error: output must differ from the HTML source", file=sys.stderr)
        return 2

    try:
        if args.check:
            validate_rendered_png(source, output)
        else:
            render(source, output, scale=args.scale)
    except (
        DiagramError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
