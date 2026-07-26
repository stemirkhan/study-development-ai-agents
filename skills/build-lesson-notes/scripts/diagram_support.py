#!/usr/bin/env python3
"""Shared validation and PNG metadata helpers for lesson diagrams."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


TEMPLATE_VERSION = "study-sketch-v1"
FINGERPRINT_KEY = "lesson-diagram-fingerprint"
VERSION_KEY = "lesson-diagram-template"
SCALE_KEY = "lesson-diagram-scale"
PIXEL_FINGERPRINT_KEY = "lesson-diagram-pixels-sha256"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SOURCE_BYTES = 512 * 1024

ALLOWED_TAGS = {
    "html",
    "head",
    "meta",
    "title",
    "link",
    "body",
    "main",
    "h1",
    "script",
}
ALLOWED_ATTRIBUTES = {
    "html": {"lang"},
    "head": set(),
    "meta": {"charset", "name", "content"},
    "title": set(),
    "link": {"rel", "data-lesson-diagram-style", "href"},
    "body": set(),
    "main": {"id", "aria-labelledby", "data-render-state"},
    "h1": {"id", "class"},
    "script": {
        "id",
        "type",
        "data-lesson-diagram-runtime",
        "src",
    },
}
ALLOWED_KINDS = {
    "default",
    "actor",
    "accent",
    "info",
    "success",
    "danger",
}
ALLOWED_DIRECTIONS = {"top-down", "left-right"}
NODE_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")


class DiagramError(ValueError):
    """Raised when a diagram source or rendered artifact is invalid."""


@dataclass(frozen=True)
class DiagramDocument:
    source: Path
    source_bytes: bytes
    title: str
    width: int
    height: int
    direction: str
    data: dict[str, Any]
    style_path: Path
    style_bytes: bytes
    runtime_path: Path
    runtime_bytes: bytes
    support_bytes: bytes
    renderer_bytes: bytes


class _DiagramHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.template_version = ""
        self.style_href = ""
        self.runtime_src = ""
        self.document_title = ""
        self._inside_title = False
        self._inside_data = False
        self._data_parts: list[str] = []
        self._data_blocks = 0
        self._runtime_scripts = 0
        self._stylesheets = 0
        self._diagram_roots = 0
        self._diagram_titles = 0

    @staticmethod
    def _attrs(items: list[tuple[str, str | None]]) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, value in items:
            values.setdefault(key, value or "")
        return values

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attribute_names = [name for name, _ in attrs]
        duplicates = sorted(
            name for name in set(attribute_names) if attribute_names.count(name) > 1
        )
        if duplicates:
            self.errors.append(
                f"duplicate attributes are not allowed on <{tag}>: "
                f"{', '.join(duplicates)}"
            )
        values = self._attrs(attrs)
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"HTML tag is not allowed: <{tag}>")
        allowed_attributes = ALLOWED_ATTRIBUTES.get(tag, set())
        unknown_attributes = sorted(set(values) - allowed_attributes)
        if unknown_attributes:
            self.errors.append(
                f"attributes are not allowed on <{tag}>: "
                f"{', '.join(unknown_attributes)}"
            )

        if tag == "meta":
            meta_name = values.get("name", "")
            if meta_name == "lesson-diagram-template":
                self.template_version = values.get("content", "")
            elif "charset" in values:
                if values["charset"].lower() != "utf-8":
                    self.errors.append("HTML charset must be utf-8")
            elif meta_name != "viewport":
                self.errors.append("unknown or unsafe meta element is not allowed")
        elif tag == "title":
            self._inside_title = True
        elif tag == "link":
            if "data-lesson-diagram-style" not in values:
                self.errors.append("only the lesson diagram stylesheet is allowed")
            if values.get("rel", "").lower() != "stylesheet":
                self.errors.append("lesson diagram link rel must be stylesheet")
            self._stylesheets += 1
            self.style_href = values.get("href", "")
        elif tag == "main":
            if values.get("id") != "lesson-diagram":
                self.errors.append("diagram main id must be lesson-diagram")
            else:
                self._diagram_roots += 1
            if values.get("aria-labelledby") != "diagram-title":
                self.errors.append(
                    "lesson-diagram must use aria-labelledby=diagram-title"
                )
            if values.get("data-render-state") != "pending":
                self.errors.append(
                    "lesson-diagram source render state must be pending"
                )
        elif tag == "h1":
            if (
                values.get("id") != "diagram-title"
                or values.get("class") != "visually-hidden"
            ):
                self.errors.append(
                    "diagram h1 must be the visually-hidden diagram-title"
                )
            else:
                self._diagram_titles += 1
        elif tag == "script":
            if values.get("id") == "diagram-data":
                if values.get("type") != "application/json" or values.get("src"):
                    self.errors.append(
                        "diagram-data must be an inline application/json script"
                    )
                self._data_blocks += 1
                self._inside_data = True
            elif "data-lesson-diagram-runtime" in values:
                self._runtime_scripts += 1
                self.runtime_src = values.get("src", "")
            else:
                self.errors.append("executable or unknown script is not allowed")

        for attribute in ("href", "src"):
            raw_url = values.get(attribute, "")
            if raw_url and not _is_local_relative_url(raw_url):
                self.errors.append(
                    f"{attribute} must reference a local relative file: {raw_url}"
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._inside_title = False
        elif tag == "script" and self._inside_data:
            self._inside_data = False

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self.document_title += data
        if self._inside_data:
            self._data_parts.append(data)

    @property
    def data_text(self) -> str:
        return "".join(self._data_parts)

    def finish(self) -> None:
        if self.template_version != TEMPLATE_VERSION:
            self.errors.append(
                f"lesson-diagram-template must be {TEMPLATE_VERSION!r}"
            )
        if self._data_blocks != 1:
            self.errors.append("HTML must contain exactly one diagram-data block")
        if self._runtime_scripts != 1:
            self.errors.append("HTML must contain exactly one diagram runtime")
        if self._stylesheets != 1:
            self.errors.append("HTML must contain exactly one diagram stylesheet")
        if self._diagram_roots != 1:
            self.errors.append("HTML must contain exactly one lesson-diagram main")
        if self._diagram_titles != 1:
            self.errors.append("HTML must contain exactly one diagram-title h1")
        if not self.document_title.strip():
            self.errors.append("HTML <title> must not be empty")


def skill_assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def _is_local_relative_url(raw_url: str) -> bool:
    parsed = urlsplit(raw_url)
    return (
        not parsed.scheme
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and not Path(unquote(parsed.path)).is_absolute()
    )


def _resolve_asset(source: Path, raw_url: str, expected_name: str) -> Path:
    if not raw_url or not _is_local_relative_url(raw_url):
        raise DiagramError(f"missing or invalid local asset URL: {raw_url!r}")
    resolved = (source.parent / unquote(urlsplit(raw_url).path)).resolve()
    expected = (skill_assets_dir() / expected_name).resolve()
    if resolved != expected:
        raise DiagramError(
            f"diagram must use bundled {expected_name}; resolved to {resolved}"
        )
    if not resolved.is_file():
        raise DiagramError(f"diagram asset does not exist: {resolved}")
    return resolved


def _validate_keys(
    item: dict[str, Any],
    *,
    allowed: set[str],
    context: str,
) -> None:
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise DiagramError(f"{context} has unknown fields: {', '.join(unknown)}")


def _validate_label(value: Any, *, context: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiagramError(f"{context} must be a non-empty string")
    label = value.strip()
    if len(label) > max_length:
        raise DiagramError(f"{context} must be at most {max_length} characters")
    if len(label.splitlines()) > 4:
        raise DiagramError(f"{context} must use at most four lines")
    return label


def validate_diagram_title(value: Any) -> str:
    return _validate_label(value, context="title", max_length=120)


def validate_diagram_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise DiagramError("diagram-data must be a JSON object")
    _validate_keys(
        data,
        allowed={"title", "width", "height", "direction", "nodes", "edges"},
        context="diagram-data",
    )

    title = validate_diagram_title(data.get("title"))
    width = data.get("width")
    height = data.get("height")
    direction = data.get("direction")
    nodes = data.get("nodes")
    edges = data.get("edges")

    if not isinstance(width, int) or isinstance(width, bool) or not 480 <= width <= 1600:
        raise DiagramError("width must be an integer from 480 to 1600")
    if (
        not isinstance(height, int)
        or isinstance(height, bool)
        or not 320 <= height <= 1200
    ):
        raise DiagramError("height must be an integer from 320 to 1200")
    if not isinstance(direction, str) or direction not in ALLOWED_DIRECTIONS:
        raise DiagramError(
            f"direction must be one of: {', '.join(sorted(ALLOWED_DIRECTIONS))}"
        )
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 24:
        raise DiagramError("nodes must contain from 1 to 24 items")
    if not isinstance(edges, list) or len(edges) > 48:
        raise DiagramError("edges must be a list with at most 48 items")

    normalized_nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    occupied_slots: set[tuple[int, int]] = set()
    for index, raw_node in enumerate(nodes):
        context = f"nodes[{index}]"
        if not isinstance(raw_node, dict):
            raise DiagramError(f"{context} must be an object")
        _validate_keys(
            raw_node,
            allowed={"id", "label", "rank", "order", "kind"},
            context=context,
        )
        node_id = raw_node.get("id")
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
            raise DiagramError(
                f"{context}.id must match {NODE_ID.pattern!r}"
            )
        if node_id in node_ids:
            raise DiagramError(f"duplicate node id: {node_id}")
        node_ids.add(node_id)
        label = _validate_label(
            raw_node.get("label"),
            context=f"{context}.label",
            max_length=120,
        )
        rank = raw_node.get("rank")
        order = raw_node.get("order")
        if not isinstance(rank, int) or isinstance(rank, bool) or not 0 <= rank <= 12:
            raise DiagramError(f"{context}.rank must be an integer from 0 to 12")
        if (
            not isinstance(order, int)
            or isinstance(order, bool)
            or not 0 <= order <= 24
        ):
            raise DiagramError(f"{context}.order must be an integer from 0 to 24")
        slot = (rank, order)
        if slot in occupied_slots:
            raise DiagramError(f"duplicate rank/order slot: {rank}/{order}")
        occupied_slots.add(slot)
        kind = raw_node.get("kind", "default")
        if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
            raise DiagramError(
                f"{context}.kind must be one of: "
                f"{', '.join(sorted(ALLOWED_KINDS))}"
            )
        normalized_node = dict(raw_node)
        normalized_node["label"] = label
        normalized_nodes.append(normalized_node)

    normalized_edges: list[dict[str, Any]] = []
    edge_slots: set[tuple[str, str, int]] = set()
    for index, raw_edge in enumerate(edges):
        context = f"edges[{index}]"
        if not isinstance(raw_edge, dict):
            raise DiagramError(f"{context} must be an object")
        _validate_keys(
            raw_edge,
            allowed={"from", "to", "label", "lane", "dashed"},
            context=context,
        )
        source = raw_edge.get("from")
        target = raw_edge.get("to")
        if not isinstance(source, str) or not NODE_ID.fullmatch(source):
            raise DiagramError(f"{context}.from must reference a valid node id")
        if not isinstance(target, str) or not NODE_ID.fullmatch(target):
            raise DiagramError(f"{context}.to must reference a valid node id")
        if source not in node_ids:
            raise DiagramError(f"{context}.from references unknown node: {source!r}")
        if target not in node_ids:
            raise DiagramError(f"{context}.to references unknown node: {target!r}")
        if source == target:
            raise DiagramError(f"{context} self-edges are not supported")
        normalized_edge = dict(raw_edge)
        if "label" in raw_edge:
            normalized_edge["label"] = _validate_label(
                raw_edge["label"],
                context=f"{context}.label",
                max_length=64,
            )
        lane = raw_edge.get("lane", 0)
        if (
            not isinstance(lane, int)
            or isinstance(lane, bool)
            or not -2 <= lane <= 2
        ):
            raise DiagramError(f"{context}.lane must be an integer from -2 to 2")
        dashed = raw_edge.get("dashed", False)
        if not isinstance(dashed, bool):
            raise DiagramError(f"{context}.dashed must be boolean")
        edge_slot = (source, target, lane)
        if edge_slot in edge_slots:
            raise DiagramError(
                f"duplicate edge/lane combination: {source} -> {target}, lane {lane}"
            )
        edge_slots.add(edge_slot)
        normalized_edges.append(normalized_edge)

    normalized = dict(data)
    normalized["title"] = title
    normalized["nodes"] = normalized_nodes
    normalized["edges"] = normalized_edges
    return normalized


def load_diagram(source: Path) -> DiagramDocument:
    path = source.expanduser().resolve()
    if path.suffix.lower() != ".html":
        raise DiagramError("diagram source must have an .html suffix")
    try:
        source_bytes = path.read_bytes()
        if len(source_bytes) > MAX_SOURCE_BYTES:
            raise DiagramError(
                f"diagram source must not exceed {MAX_SOURCE_BYTES} bytes"
            )
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiagramError("diagram source must be valid UTF-8") from exc
    except OSError as exc:
        raise DiagramError(str(exc)) from exc

    parser = _DiagramHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise DiagramError(f"invalid HTML: {exc}") from exc
    parser.finish()
    if parser.errors:
        raise DiagramError("; ".join(dict.fromkeys(parser.errors)))

    try:
        raw_data = json.loads(parser.data_text)
    except json.JSONDecodeError as exc:
        raise DiagramError(
            f"diagram-data is not valid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    data = validate_diagram_data(raw_data)

    style_path = _resolve_asset(path, parser.style_href, "diagram.css")
    runtime_path = _resolve_asset(path, parser.runtime_src, "diagram.js")
    try:
        style_bytes = style_path.read_bytes()
        runtime_bytes = runtime_path.read_bytes()
        support_bytes = Path(__file__).read_bytes()
        renderer_bytes = Path(__file__).with_name("render_diagram.py").read_bytes()
    except OSError as exc:
        raise DiagramError(str(exc)) from exc

    return DiagramDocument(
        source=path,
        source_bytes=source_bytes,
        title=data["title"],
        width=data["width"],
        height=data["height"],
        direction=data["direction"],
        data=data,
        style_path=style_path,
        style_bytes=style_bytes,
        runtime_path=runtime_path,
        runtime_bytes=runtime_bytes,
        support_bytes=support_bytes,
        renderer_bytes=renderer_bytes,
    )


def diagram_fingerprint(document: DiagramDocument) -> str:
    digest = hashlib.sha256()
    for marker, payload in (
        (b"source", document.source_bytes),
        (b"style", document.style_bytes),
        (b"runtime", document.runtime_bytes),
        (b"support", document.support_bytes),
        (b"renderer", document.renderer_bytes),
    ):
        digest.update(marker)
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    digest.update(TEMPLATE_VERSION.encode("ascii"))
    return digest.hexdigest()


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise DiagramError("file is not a PNG")
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise DiagramError("PNG contains a truncated chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise DiagramError("PNG contains a truncated chunk payload")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise DiagramError(f"PNG chunk has an invalid CRC: {chunk_type!r}")
        chunks.append((chunk_type, payload))
        offset = end
        if chunk_type == b"IEND":
            saw_iend = True
            break
    if not saw_iend or offset != len(data):
        raise DiagramError("PNG has missing IEND or trailing data")
    return chunks


def _encode_png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", crc)
    )


def add_png_text(data: bytes, metadata: dict[str, str]) -> bytes:
    chunks = _png_chunks(data)
    encoded: list[tuple[bytes, bytes]] = []
    managed_keys = set(metadata)
    inserted = False

    for chunk_type, payload in chunks:
        if chunk_type == b"tEXt":
            raw_key, separator, _ = payload.partition(b"\0")
            if separator and raw_key.decode("latin-1") in managed_keys:
                continue
        encoded.append((chunk_type, payload))
        if chunk_type == b"IHDR":
            for key, value in metadata.items():
                if not key or "\0" in key or len(key.encode("latin-1")) > 79:
                    raise DiagramError(f"invalid PNG text key: {key!r}")
                try:
                    text_payload = (
                        key.encode("latin-1")
                        + b"\0"
                        + value.encode("latin-1")
                    )
                except UnicodeEncodeError as exc:
                    raise DiagramError("PNG metadata must be Latin-1") from exc
                encoded.append((b"tEXt", text_payload))
            inserted = True

    if not inserted:
        raise DiagramError("PNG is missing IHDR")
    return PNG_SIGNATURE + b"".join(
        _encode_png_chunk(chunk_type, payload)
        for chunk_type, payload in encoded
    )


def read_png_text(path: Path) -> dict[str, str]:
    try:
        chunks = _png_chunks(path.read_bytes())
    except OSError as exc:
        raise DiagramError(str(exc)) from exc
    metadata: dict[str, str] = {}
    for chunk_type, payload in chunks:
        if chunk_type != b"tEXt":
            continue
        raw_key, separator, raw_value = payload.partition(b"\0")
        if not separator:
            continue
        metadata[raw_key.decode("latin-1")] = raw_value.decode("latin-1")
    return metadata


def png_dimensions(data: bytes) -> tuple[int, int]:
    chunks = _png_chunks(data)
    for chunk_type, payload in chunks:
        if chunk_type == b"IHDR":
            if len(payload) != 13:
                raise DiagramError("PNG IHDR has an invalid length")
            return struct.unpack(">II", payload[:8])
    raise DiagramError("PNG is missing IHDR")


def png_pixel_fingerprint(data: bytes) -> str:
    digest = hashlib.sha256()
    saw_pixels = False
    for chunk_type, payload in _png_chunks(data):
        if chunk_type in {b"IHDR", b"IDAT"}:
            digest.update(chunk_type)
            digest.update(struct.pack(">I", len(payload)))
            digest.update(payload)
        if chunk_type == b"IDAT":
            saw_pixels = True
    if not saw_pixels:
        raise DiagramError("PNG is missing IDAT")
    return digest.hexdigest()


def validate_rendered_png(source: Path, output: Path) -> None:
    document = load_diagram(source)
    if not output.is_file():
        raise DiagramError(f"rendered PNG does not exist: {output}")
    metadata = read_png_text(output)
    actual_version = metadata.get(VERSION_KEY)
    if actual_version != TEMPLATE_VERSION:
        raise DiagramError(
            f"PNG template version is {actual_version!r}; expected {TEMPLATE_VERSION!r}"
        )
    expected = diagram_fingerprint(document)
    actual = metadata.get(FINGERPRINT_KEY)
    if actual != expected:
        raise DiagramError("PNG is stale; render it again from the HTML source")
    raw_scale = metadata.get(SCALE_KEY)
    if raw_scale not in {"1", "2", "3"}:
        raise DiagramError("PNG has missing or invalid render scale metadata")
    scale = int(raw_scale)
    try:
        png_data = output.read_bytes()
    except OSError as exc:
        raise DiagramError(str(exc)) from exc
    actual_size = png_dimensions(png_data)
    expected_size = (document.width * scale, document.height * scale)
    if actual_size != expected_size:
        raise DiagramError(
            f"PNG dimensions are {actual_size[0]}x{actual_size[1]}; "
            f"expected {expected_size[0]}x{expected_size[1]}"
        )
    expected_pixels = metadata.get(PIXEL_FINGERPRINT_KEY)
    actual_pixels = png_pixel_fingerprint(png_data)
    if expected_pixels != actual_pixels:
        raise DiagramError("PNG pixels differ from the recorded render")
