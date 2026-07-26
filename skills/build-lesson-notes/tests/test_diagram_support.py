from __future__ import annotations

import json
import os
import subprocess
import struct
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

import render_diagram as render_module
from create_diagram import render_template
from diagram_support import (
    FINGERPRINT_KEY,
    PIXEL_FINGERPRINT_KEY,
    SCALE_KEY,
    TEMPLATE_VERSION,
    VERSION_KEY,
    DiagramError,
    add_png_text,
    diagram_fingerprint,
    load_diagram,
    png_pixel_fingerprint,
    read_png_text,
    skill_assets_dir,
    validate_rendered_png,
)
from render_diagram import require_render_ready


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", crc)
    )


def tiny_png(
    width: int = 1,
    height: int = 1,
    pixel: bytes = b"\xff\xff\xff\xff",
) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00" + pixel)
    return (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


def write_diagram(
    directory: Path,
    *,
    data: dict[str, object] | None = None,
    extra_html: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "runtime.html"
    assets = skill_assets_dir()
    style_href = Path(os.path.relpath(assets / "diagram.css", directory)).as_posix()
    runtime_href = Path(os.path.relpath(assets / "diagram.js", directory)).as_posix()
    payload = data or {
        "title": "Agent runtime",
        "width": 720,
        "height": 480,
        "direction": "top-down",
        "nodes": [
            {
                "id": "user",
                "label": "User",
                "rank": 0,
                "order": 0,
                "kind": "actor",
            },
            {
                "id": "loop",
                "label": "Agent Loop",
                "rank": 1,
                "order": 0,
                "kind": "accent",
            },
        ],
        "edges": [
            {
                "from": "user",
                "to": "loop",
                "label": "Prompt",
                "lane": 0,
            }
        ],
    }
    source.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="lesson-diagram-template" content="{TEMPLATE_VERSION}">
  <title>{payload["title"]}</title>
  <link rel="stylesheet" data-lesson-diagram-style href="{style_href}">
</head>
<body>
  <main
    id="lesson-diagram"
    aria-labelledby="diagram-title"
    data-render-state="pending"
  >
    <h1 id="diagram-title" class="visually-hidden">{payload["title"]}</h1>
    <script id="diagram-data" type="application/json">
{json.dumps(payload)}
    </script>
  </main>
  <script data-lesson-diagram-runtime src="{runtime_href}"></script>
  {extra_html}
</body>
</html>
""",
        encoding="utf-8",
    )
    return source


def test_load_diagram_accepts_ranked_flow(tmp_path: Path) -> None:
    document = load_diagram(write_diagram(tmp_path))

    assert document.title == "Agent runtime"
    assert document.width == 720
    assert document.direction == "top-down"
    assert document.style_path.name == "diagram.css"


def test_load_diagram_normalizes_labels_for_snapshot(tmp_path: Path) -> None:
    data = {
        "title": "  Agent runtime  ",
        "width": 720,
        "height": 480,
        "direction": "top-down",
        "nodes": [
            {
                "id": "user",
                "label": "  User  ",
                "rank": 0,
                "order": 0,
                "kind": "actor",
            },
            {
                "id": "loop",
                "label": "  Agent Loop  ",
                "rank": 1,
                "order": 0,
                "kind": "accent",
            },
        ],
        "edges": [
            {
                "from": "user",
                "to": "loop",
                "label": "  Prompt  ",
            }
        ],
    }

    document = load_diagram(write_diagram(tmp_path, data=data))

    assert document.data["title"] == "Agent runtime"
    assert document.data["nodes"][0]["label"] == "User"
    assert document.data["edges"][0]["label"] == "Prompt"


def test_create_template_escapes_html_and_json_title() -> None:
    rendered = render_template(
        "{{TITLE}}\n{{TITLE_JSON}}\n{{STYLE_HREF}}\n{{RUNTIME_HREF}}\n",
        title='Agent </script><script>alert("x")</script>',
        style_href="../../diagram.css",
        runtime_href="../../diagram.js",
    )

    assert "&lt;/script&gt;" in rendered
    assert "\\u003c/script\\u003e" in rendered
    assert '"><script>' not in rendered


def test_create_template_does_not_replace_tokens_inside_title() -> None:
    rendered = render_template(
        "{{TITLE}}\n{{TITLE_JSON}}\n{{STYLE_HREF}}\n{{RUNTIME_HREF}}\n",
        title="Flow {{STYLE_HREF}}",
        style_href="../../diagram.css",
        runtime_href="../../diagram.js",
    )

    assert "Flow {{STYLE_HREF}}" in rendered


def test_create_diagram_rejects_title_over_contract_limit(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_diagram.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(note),
            "--name",
            "runtime",
            "--title",
            "x" * 121,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "at most 120 characters" in result.stderr
    assert not (tmp_path / "diagrams" / "runtime.html").exists()


def test_create_diagram_rejects_symlinked_parent(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "diagrams").symlink_to(outside, target_is_directory=True)
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_diagram.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(note),
            "--name",
            "runtime",
            "--title",
            "Runtime",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "symlinked diagrams directory" in result.stderr
    assert not (outside / "runtime.html").exists()


def test_load_diagram_rejects_dangling_edge(tmp_path: Path) -> None:
    data = {
        "title": "Broken graph",
        "width": 720,
        "height": 480,
        "direction": "left-right",
        "nodes": [
            {
                "id": "start",
                "label": "Start",
                "rank": 0,
                "order": 0,
                "kind": "default",
            }
        ],
        "edges": [{"from": "start", "to": "missing"}],
    }

    with pytest.raises(DiagramError, match="unknown node"):
        load_diagram(write_diagram(tmp_path, data=data))


def test_load_diagram_rejects_non_string_edge_id(tmp_path: Path) -> None:
    data = {
        "title": "Broken graph",
        "width": 720,
        "height": 480,
        "direction": "top-down",
        "nodes": [
            {
                "id": "start",
                "label": "Start",
                "rank": 0,
                "order": 0,
                "kind": "default",
            }
        ],
        "edges": [{"from": [], "to": "start"}],
    }

    with pytest.raises(DiagramError, match="valid node id"):
        load_diagram(write_diagram(tmp_path, data=data))


def test_load_diagram_rejects_executable_script(tmp_path: Path) -> None:
    source = write_diagram(tmp_path, extra_html="<script>alert(1)</script>")

    with pytest.raises(DiagramError, match="script is not allowed"):
        load_diagram(source)


def test_load_diagram_rejects_duplicate_runtime_src(tmp_path: Path) -> None:
    source = write_diagram(tmp_path)
    text = source.read_text(encoding="utf-8")
    text = text.replace(
        "<script data-lesson-diagram-runtime src=",
        '<script data-lesson-diagram-runtime src="https://attacker.invalid/x.js" src=',
    )
    source.write_text(text, encoding="utf-8")

    with pytest.raises(DiagramError, match="duplicate attributes"):
        load_diagram(source)


def test_render_ready_ignores_spoofed_comment() -> None:
    dumped = """
    <!-- data-render-state="ready" -->
    <main
      id="lesson-diagram"
      data-render-state="error"
      data-render-error="layout is too dense"
    ></main>
    """

    with pytest.raises(RuntimeError, match="layout is too dense"):
        require_render_ready(dumped)


def test_load_diagram_rejects_duplicate_lesson_root(tmp_path: Path) -> None:
    source = write_diagram(
        tmp_path,
        extra_html=(
            '<main id="lesson-diagram" aria-labelledby="diagram-title" '
            'data-render-state="ready"></main>'
        ),
    )

    with pytest.raises(DiagramError, match="render state must be pending"):
        load_diagram(source)


def test_load_diagram_rejects_meta_redirect(tmp_path: Path) -> None:
    source = write_diagram(
        tmp_path,
        extra_html='<meta http-equiv="refresh" content="0; https://example.com">',
    )

    with pytest.raises(DiagramError, match="unsafe meta"):
        load_diagram(source)


def test_load_diagram_rejects_meta_redirect_mixed_with_viewport(
    tmp_path: Path,
) -> None:
    source = write_diagram(
        tmp_path,
        extra_html=(
            '<meta name="viewport" http-equiv="refresh" '
            'content="0; https://attacker.invalid">'
        ),
    )

    with pytest.raises(DiagramError, match="attributes are not allowed"):
        load_diagram(source)


def test_png_fingerprint_round_trip_and_staleness(tmp_path: Path) -> None:
    source = write_diagram(tmp_path)
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

    validate_rendered_png(source, output)
    assert read_png_text(output)[VERSION_KEY] == TEMPLATE_VERSION

    source.write_text(
        source.read_text(encoding="utf-8") + "\n<!-- source changed -->\n",
        encoding="utf-8",
    )
    with pytest.raises(DiagramError, match="stale"):
        validate_rendered_png(source, output)


def test_fingerprint_includes_support_module(tmp_path: Path) -> None:
    document = load_diagram(write_diagram(tmp_path))

    changed = replace(document, support_bytes=document.support_bytes + b"\n")

    assert diagram_fingerprint(changed) != diagram_fingerprint(document)


def test_validate_rendered_png_detects_changed_pixels(tmp_path: Path) -> None:
    source = write_diagram(tmp_path)
    document = load_diagram(source)
    original = tiny_png(1440, 960)
    changed = tiny_png(1440, 960, pixel=b"\x00\x00\x00\xff")
    output = source.with_suffix(".png")
    output.write_bytes(
        add_png_text(
            changed,
            {
                FINGERPRINT_KEY: diagram_fingerprint(document),
                PIXEL_FINGERPRINT_KEY: png_pixel_fingerprint(original),
                SCALE_KEY: "2",
                VERSION_KEY: TEMPLATE_VERSION,
            },
        )
    )

    with pytest.raises(DiagramError, match="pixels differ"):
        validate_rendered_png(source, output)


def test_render_aborts_if_source_changes_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_diagram(tmp_path)
    output = source.with_suffix(".png")
    output.write_bytes(b"previous-render")

    def fake_chromium(
        command: list[str],
        *,
        timeout: int = 45,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        screenshot = next(
            (
                argument.split("=", 1)[1]
                for argument in command
                if argument.startswith("--screenshot=")
            ),
            "",
        )
        if screenshot:
            png = add_png_text(
                tiny_png(1440, 960),
                {"padding": "x" * 1200},
            )
            Path(screenshot).write_bytes(png)
            source.write_text(
                source.read_text(encoding="utf-8") + "\n<!-- changed -->\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        dumped = (
            '<main id="lesson-diagram" '
            'data-render-state="ready"></main>'
        )
        return subprocess.CompletedProcess(command, 0, dumped, "")

    monkeypatch.setattr(render_module, "find_chromium", lambda: "chromium")
    monkeypatch.setattr(render_module, "_run_chromium", fake_chromium)

    with pytest.raises(RuntimeError, match="changed during render"):
        render_module.render(source, output, scale=2)

    assert output.read_bytes() == b"previous-render"


def test_render_preserves_output_when_staged_png_has_wrong_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_diagram(tmp_path)
    output = source.with_suffix(".png")
    output.write_bytes(b"previous-render")

    def fake_chromium(
        command: list[str],
        *,
        timeout: int = 45,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        screenshot = next(
            (
                argument.split("=", 1)[1]
                for argument in command
                if argument.startswith("--screenshot=")
            ),
            "",
        )
        if screenshot:
            png = add_png_text(
                tiny_png(10, 10),
                {"padding": "x" * 1200},
            )
            Path(screenshot).write_bytes(png)
            return subprocess.CompletedProcess(command, 0, "", "")
        dumped = (
            '<main id="lesson-diagram" '
            'data-render-state="ready"></main>'
        )
        return subprocess.CompletedProcess(command, 0, dumped, "")

    monkeypatch.setattr(render_module, "find_chromium", lambda: "chromium")
    monkeypatch.setattr(render_module, "_run_chromium", fake_chromium)

    with pytest.raises(DiagramError, match="dimensions"):
        render_module.render(source, output, scale=2)

    assert output.read_bytes() == b"previous-render"
