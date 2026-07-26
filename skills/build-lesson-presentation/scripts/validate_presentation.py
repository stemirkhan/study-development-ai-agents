#!/usr/bin/env python3
"""Validate lesson presentations without loading them in a browser."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


EXTERNAL_SCHEMES = {"http", "https"}
RESOURCE_ATTRIBUTES = {
    ("audio", "src"),
    ("embed", "src"),
    ("form", "action"),
    ("iframe", "src"),
    ("img", "src"),
    ("input", "formaction"),
    ("input", "src"),
    ("link", "href"),
    ("object", "data"),
    ("script", "src"),
    ("source", "src"),
    ("track", "src"),
    ("video", "poster"),
    ("video", "src"),
}


@dataclass
class Slide:
    tag: str
    line: int
    has_heading: bool = False


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


def is_external_url(value: str) -> bool:
    value = value.strip()
    if value.startswith("//"):
        return True
    return urlsplit(value).scheme.lower() in EXTERNAL_SCHEMES


class PresentationParser(HTMLParser):
    def __init__(self, findings: Findings) -> None:
        super().__init__(convert_charrefs=True)
        self.findings = findings
        self.has_doctype = False
        self.html_lang = ""
        self.has_charset = False
        self.has_viewport = False
        self.in_title = False
        self.in_script = False
        self.in_style = False
        self.title_parts: list[str] = []
        self.script_parts: list[str] = []
        self.style_parts: list[str] = []
        self.ids: dict[str, int] = {}
        self.internal_links: list[tuple[str, int]] = []
        self.slides: list[Slide] = []
        self.slide_stack: list[int] = []
        self.interactive_controls = 0
        self.nav_signals: set[str] = set()
        self.has_aria_live = False

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() == "doctype html":
            self.has_doctype = True

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attrs = {name.lower(): value or "" for name, value in attrs_list}
        line, _ = self.getpos()

        if tag == "html":
            self.html_lang = attrs.get("lang", "").strip()
        elif tag == "meta":
            if attrs.get("charset", "").strip().lower() == "utf-8":
                self.has_charset = True
            if attrs.get("name", "").strip().lower() == "viewport":
                self.has_viewport = bool(attrs.get("content", "").strip())
        elif tag == "title":
            self.in_title = True
        elif tag == "script":
            self.in_script = True
        elif tag == "style":
            self.in_style = True

        if "style" in attrs:
            self.style_parts.append(attrs["style"])

        element_id = attrs.get("id", "").strip()
        if element_id:
            if element_id in self.ids:
                self.findings.error(
                    f"line {line}: duplicate id {element_id!r} "
                    f"(first seen on line {self.ids[element_id]})"
                )
            else:
                self.ids[element_id] = line

        href = attrs.get("href", "").strip()
        if href.startswith("#"):
            target = href[1:]
            if target:
                self.internal_links.append((target, line))
            else:
                self.findings.warning(f"line {line}: empty internal anchor")
        elif tag == "a" and is_external_url(href):
            self.findings.warning(
                f"line {line}: external hyperlink will require network access: {href}"
            )

        for name, value in attrs.items():
            if (tag, name) in RESOURCE_ATTRIBUTES and is_external_url(value):
                self.findings.error(
                    f"line {line}: external resource is not self-contained: "
                    f"{tag}[{name}]={value!r}"
                )

        classes = set(attrs.get("class", "").split())
        if "slide" in classes:
            slide = Slide(tag=tag, line=line)
            self.slides.append(slide)
            self.slide_stack.append(len(self.slides) - 1)

        if self.slide_stack and re.fullmatch(r"h[1-6]", tag):
            self.slides[self.slide_stack[-1]].has_heading = True

        if tag == "img":
            if "alt" not in attrs:
                self.findings.error(f"line {line}: img is missing an alt attribute")
            elif (
                not attrs["alt"].strip()
                and attrs.get("aria-hidden", "").lower() != "true"
                and attrs.get("role", "").lower() not in {"none", "presentation"}
            ):
                self.findings.warning(
                    f"line {line}: empty img alt should be reserved for "
                    "explicitly decorative images"
                )

        if tag in {"button", "input", "select", "textarea"}:
            self.interactive_controls += 1
            signal = " ".join(
                attrs.get(name, "")
                for name in ("id", "class", "aria-label", "title", "data-action")
            ).lower()
            if re.search(r"\b(prev|previous|back|назад)\b", signal):
                self.nav_signals.add("previous")
            if re.search(r"\b(next|forward|далее|впер[её]д)\b", signal):
                self.nav_signals.add("next")

        if "aria-live" in attrs:
            self.has_aria_live = True

        tabindex = attrs.get("tabindex", "").strip()
        if tabindex:
            try:
                if int(tabindex) > 0:
                    self.findings.error(
                        f"line {line}: positive tabindex disrupts natural focus order"
                    )
            except ValueError:
                self.findings.error(
                    f"line {line}: tabindex must be an integer, got {tabindex!r}"
                )
        if "autofocus" in attrs:
            self.findings.error(
                f"line {line}: autofocus may move focus unexpectedly"
            )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_script = False
        elif tag == "style":
            self.in_style = False
        if self.slide_stack and self.slides[self.slide_stack[-1]].tag == tag:
            self.slide_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_script:
            self.script_parts.append(data)
        if self.in_style:
            self.style_parts.append(data)


def validate_document(text: str) -> Findings:
    findings = Findings()
    parser = PresentationParser(findings)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser errors are rare, but must be reported.
        findings.error(f"HTML parsing failed: {exc}")
        return findings

    if not parser.has_doctype:
        findings.error("missing <!doctype html>")
    if not parser.html_lang:
        findings.error("html element is missing a non-empty lang attribute")
    if not parser.has_charset:
        findings.error("missing <meta charset=\"utf-8\">")
    if not parser.has_viewport:
        findings.error("missing a non-empty viewport meta tag")
    if not "".join(parser.title_parts).strip():
        findings.error("missing a non-empty title element")

    if not parser.slides:
        findings.error("no elements with class .slide found")
    for number, slide in enumerate(parser.slides, start=1):
        if slide.tag != "section":
            findings.error(
                f"line {slide.line}: slide {number} must use a section element"
            )
        if not slide.has_heading:
            findings.error(
                f"line {slide.line}: slide {number} has no h1-h6 heading"
            )

    for target, line in parser.internal_links:
        if target not in parser.ids:
            findings.error(
                f"line {line}: internal anchor references missing id {target!r}"
            )

    script_text = "\n".join(parser.script_parts)
    style_text = "\n".join(parser.style_parts)
    if not re.search(r"@media\s+print\b", style_text, flags=re.IGNORECASE):
        findings.error("missing print CSS (@media print)")
    if "prefers-reduced-motion" not in style_text.lower():
        findings.error("missing prefers-reduced-motion support")
    if not parser.has_aria_live:
        findings.error("missing an aria-live status region")

    if not re.search(r"(?:keydown|keyup)", script_text, flags=re.IGNORECASE):
        findings.error("missing keyboard navigation handler")
    if not re.search(
        r"(?:click|pointer(?:down|up)|touch(?:start|end))",
        script_text,
        flags=re.IGNORECASE,
    ):
        findings.error("missing pointer or touch navigation handler")
    if parser.interactive_controls < 2 or parser.nav_signals != {"previous", "next"}:
        findings.error(
            "missing recognizable previous and next navigation controls"
        )

    forbidden_patterns = {
        "fetch()": r"\bfetch\s*\(",
        "XMLHttpRequest": r"\bXMLHttpRequest\b",
        "WebSocket": r"\bWebSocket\b",
        "EventSource": r"\bEventSource\b",
        "dynamic import": r"\bimport\s*\(",
        "JavaScript import": r"(?m)^\s*import\s+(?:[\w*{]|[\"'])",
        "service worker": r"\bserviceWorker\b",
    }
    for label, pattern in forbidden_patterns.items():
        if re.search(pattern, script_text, flags=re.IGNORECASE):
            findings.error(f"forbidden network/runtime API found: {label}")

    for match in re.finditer(
        r"(?:url\s*\(\s*[\"']?|@import\s+(?:url\s*\(\s*)?[\"']?)"
        r"((?:https?:)?//[^\"')\s;]+)",
        style_text,
        flags=re.IGNORECASE,
    ):
        findings.error(
            f"external CSS resource is not self-contained: {match.group(1)!r}"
        )

    placeholder_patterns = (
        r"\{\{[^{}\n]+\}\}",
        r"__[A-Z][A-Z0-9_]+__",
    )
    for pattern in placeholder_patterns:
        for match in re.finditer(pattern, text):
            findings.error(f"unresolved template placeholder: {match.group(0)!r}")

    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate one or more self-contained HTML presentations."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="HTML files to validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    has_errors = False

    for path_arg in args.paths:
        path = path_arg.expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(f"{path}: ERROR: cannot read UTF-8 HTML: {exc}")
            has_errors = True
            continue

        findings = validate_document(text)
        for message in sorted(findings.errors):
            print(f"{path}: ERROR: {message}")
        for message in sorted(findings.warnings):
            print(f"{path}: WARNING: {message}")
        if not findings.errors and not findings.warnings:
            print(f"{path}: OK")
        has_errors = has_errors or bool(findings.errors)

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
