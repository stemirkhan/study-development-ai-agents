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

from diagram_support import DiagramError, load_diagram, validate_rendered_png

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
ALLOWED_EXACT_ENGLISH_NAMES = re.compile(
    r"\b(?:OpenAI\s+Responses(?:\s+API)?|OpenAI\s+Realtime|"
    r"OpenAI\s+Models\s+API|Anthropic\s+Messages(?:\s+API)?|"
    r"Anthropic\s+Models\s+API|Anthropic\s+API|Gemini\s+Models\s+API|"
    r"Gemini\s+API|Google\s+Gemini|Interactions\s+API|JSON\s+Schema|"
    r"Structured\s+Outputs|Fine-grained\s+tool\s+streaming|"
    r"Streaming\s+responses)\b"
)
DISCOURAGED_ENGLISH_PROSE = {
    "LLM": "языковая модель",
    "Agent": "агент",
    "tool call": "вызов инструмента",
    "tool": "инструмент",
    "prompt": "инструкция или запрос",
    "context": "контекст",
    "model": "модель",
    "response": "ответ",
    "fallback": "резервный маршрут или переключение",
    "route": "маршрут",
    "router": "маршрутизатор",
    "routing": "маршрутизация",
    "capability": "возможность",
    "requirements": "требования",
    "preference": "критерий предпочтения",
    "profile": "профиль",
    "match": "соответствие",
    "gap": "несоответствие",
    "provider": "поставщик",
    "availability": "доступность",
    "semantic": "смысловой",
    "typed": "типизированный",
    "fail-closed": "отказ при неопределённости",
    "best-effort": "без гарантии",
    "mutable": "изменяемый",
    "immutable": "неизменяемый",
    "versioned": "версионированный",
    "experimental feature": "экспериментальная возможность",
    "constraint": "ограничение",
    "mode": "режим",
    "input": "входные данные",
    "output": "выходные данные или ответ",
    "modality": "тип данных",
    "stream": "поток",
    "streaming": "потоковая выдача",
    "event": "событие",
    "deadline": "крайний срок",
    "budget": "лимит",
    "side effect": "побочный эффект",
    "execution flow": "поток выполнения",
    "execution authority": "полномочия на выполнение",
    "server-side": "на стороне сервера",
    "client-side": "на стороне клиента",
    "reconciliation": "сверка состояния",
    "allowlist": "список разрешённых значений",
    "authorization": "проверка полномочий",
    "idempotency": "идемпотентность",
    "registry": "реестр",
    "ranking": "ранжирование",
    "health": "работоспособность",
    "latency": "задержка",
    "cost": "стоимость",
    "quality": "качество",
    "candidate": "кандидат",
    "eligible": "допустимый",
    "terminal": "завершённый",
    "partial": "частичный или оборванный",
    "hidden": "скрытый",
    "transparent": "неявный",
    "upper layer": "вызывающий слой",
    "upper runtime": "вызывающий слой",
    "state": "состояние",
    "decision": "решение",
    "proposal": "предложение",
    "exchange": "запрос к поставщику",
    "request": "запрос",
    "application": "приложение",
    "template": "шаблон",
    "metadata": "метаданные",
    "manifest": "описание возможностей",
    "provenance": "источник сведений",
    "probe": "контрольный запрос",
    "preflight": "предварительная проверка",
    "snapshot": "снимок версии",
    "alias": "псевдоним",
    "endpoint": "точка API",
    "header": "заголовок",
    "entitlement": "доступ учётной записи",
    "policy": "политика",
    "retry": "повторная попытка",
    "failure": "сбой",
    "report": "отчёт",
    "trace": "трасса или журнал",
    "replay": "воспроизведение",
    "audit": "аудит",
    "production": "промышленная среда",
    "static": "статический",
    "deterministic": "детерминированный",
    "live": "реальный",
    "property test": "тест свойств",
    "framework": "фреймворк",
    "SDK": "набор разработчика",
    "UI": "интерфейс",
    "prefix": "начальный фрагмент",
    "history": "история",
    "assistant message": "сообщение ассистента",
    "terminal response": "завершённый ответ",
    "committed": "подтверждённый",
    "provisional": "предварительный",
    "cancellation": "отмена",
    "cleanup": "освобождение ресурсов",
    "runtime": "среда выполнения",
    "adapter": "адаптер",
    "source": "источник",
    "consumer": "потребитель",
    "chunk": "фрагмент",
    "attempt": "попытка",
    "intent": "намерение",
    "timeout": "истечение времени ожидания",
    "backpressure": "обратное давление",
}


def _english_phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = phrase.split()
    joined = r"[- ]".join(re.escape(word) for word in words)
    return re.compile(rf"\b{joined}s?\b", re.IGNORECASE)


DISCOURAGED_ENGLISH_PATTERNS = tuple(
    (_english_phrase_pattern(term), term, replacement)
    for term, replacement in DISCOURAGED_ENGLISH_PROSE.items()
)

ALLOWED_LATIN_PROSE_TOKENS = {
    "Anthropic",
    "Gemini",
    "Google",
    "Python",
    "Workflow",
}
LATIN_PROSE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Za-z][A-Za-z0-9_]*(?:[.:+/-][A-Za-z0-9_]+)*)"
    r"(?![A-Za-z0-9_])"
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
    without_inline = re.sub(r"`[^`]+`", "", without_fences)
    return re.sub(r"<!--.*?-->", "", without_inline, flags=re.DOTALL)


def prose_for_language_check(body: str) -> str:
    prose = prose_without_code(body)
    prose = re.sub(
        r"(?<=\])\((?:\\.|[^()\n]|\([^()\n]*\))*\)",
        "",
        prose,
    )
    prose = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", prose, flags=re.MULTILINE)
    prose = re.sub(r"<https?://[^>]+>", "", prose)
    return re.sub(r"https?://\S+", "", prose)


def discouraged_english(text: str) -> list[tuple[str, str]]:
    text = ALLOWED_EXACT_ENGLISH_NAMES.sub("", text)
    return [
        (term, replacement)
        for pattern, term, replacement in DISCOURAGED_ENGLISH_PATTERNS
        if pattern.search(text)
    ]


def _looks_like_exact_identifier(token: str) -> bool:
    if token in ALLOWED_LATIN_PROSE_TOKENS:
        return True
    if len(token) == 1 and token.isupper():
        return True
    if token.isupper() and len(token) >= 2:
        return True
    if any(character.isdigit() or character in "_.:+/" for character in token):
        return True
    uppercase_positions = [
        index for index, character in enumerate(token) if character.isupper()
    ]
    return len(uppercase_positions) >= 2


def unapproved_latin_prose(text: str) -> list[str]:
    remaining = ALLOWED_EXACT_ENGLISH_NAMES.sub("", text)
    for pattern, _, _ in DISCOURAGED_ENGLISH_PATTERNS:
        remaining = pattern.sub("", remaining)
    return sorted(
        {
            match.group(1)
            for match in LATIN_PROSE_TOKEN.finditer(remaining)
            if not _looks_like_exact_identifier(match.group(1))
        },
        key=str.casefold,
    )


def validate_language(
    text: str,
    *,
    status: str,
    findings: Findings,
    context: str,
) -> None:
    matches = discouraged_english(text)
    unknown = unapproved_latin_prose(text)
    if not matches and not unknown:
        return
    details = [
        f"{term} → «{replacement}»" for term, replacement in matches
    ]
    if unknown:
        details.append(
            "неразрешённая латиница → " + ", ".join(unknown)
        )
    message = f"англицизмы в {context}: {'; '.join(details)}"
    if status == "complete":
        findings.error(message)
    else:
        findings.warning(message)


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


def validate_images(
    body: str,
    note_path: Path | None,
    findings: Findings,
    *,
    status: str,
) -> None:
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
                document = load_diagram(source)
            except DiagramError as exc:
                findings.error(f"invalid diagram {raw_target}: {exc}")
                continue
            labels = [document.title]
            labels.extend(node["label"] for node in document.data["nodes"])
            labels.extend(
                edge.get("label", "") for edge in document.data["edges"]
            )
            validate_language(
                "\n".join(labels),
                status=status,
                findings=findings,
                context=f"подписях схемы {raw_target}",
            )


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
    validate_language(
        prose_for_language_check(body),
        status=status,
        findings=findings,
        context="связном тексте",
    )
    validate_images(body, note_path, findings, status=status)

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
