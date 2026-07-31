#!/usr/bin/env python3
"""Evidence-backed progress tracker for the learning repository."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Protocol, TextIO, cast

JsonObject = dict[str, object]
Evidence = dict[str, object]

SCHEMA_VERSION = 1
STAGES = (
    "theory",
    "implementation",
    "verification",
    "guided_code_tour",
    "understanding_check",
    "notes_finalized",
)
STATUSES = {"pending", "in_progress", "blocked", "complete"}
REVIEW_RESULTS = {"partial", "passed", "failed"}
UNDERSTANDING_REVIEW_TYPES = {
    "architecture_decision",
    "failure_analysis",
    "change_impact",
    "security_boundary",
    "baseline_comparison",
    "operational_readiness",
}
UNDERSTANDING_PROOF_KINDS = {"test", "trace", "diff", "metric", "experiment"}
UNDERSTANDING_POLICY_VERSION = 1
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROGRESS_PATH = REPO_ROOT / "progress" / "progress.json"


class ProgressError(ValueError):
    """The document or requested state transition violates an invariant."""


class RepositoryProbe(Protocol):
    def commit_oid(self, revision: str) -> str | None: ...

    def head_oid(self) -> str | None: ...

    def file_sha256(self, revision: str, path: str) -> str | None: ...

    def tag_target_oid(self, tag: str) -> str | None: ...


class GitRepositoryProbe:
    """Read Git evidence without mutating the repository."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def commit_oid(self, revision: str) -> str | None:
        if not COMMIT_RE.fullmatch(revision):
            return None
        return self._git("rev-parse", "--verify", f"{revision}^{{commit}}")

    def head_oid(self) -> str | None:
        return self._git("rev-parse", "--verify", "HEAD^{commit}")

    def file_sha256(self, revision: str, path: str) -> str | None:
        if self.commit_oid(revision) is None:
            return None
        completed = subprocess.run(
            ("git", "-C", str(self._root), "show", f"{revision}:{path}"),
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            return None
        return hashlib.sha256(completed.stdout).hexdigest()

    def tag_target_oid(self, tag: str) -> str | None:
        if not valid_tag(tag):
            return None
        return self._git(
            "rev-parse",
            "--verify",
            f"refs/tags/{tag}^{{commit}}",
        )

    def _git(self, *arguments: str) -> str | None:
        completed = subprocess.run(
            ("git", "-C", str(self._root), *arguments),
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None


def valid_tag(tag: str) -> bool:
    return (
        bool(TAG_RE.fullmatch(tag))
        and ".." not in tag
        and "@{" not in tag
        and not tag.endswith(("/", "."))
        and "/." not in tag
    )


def _need(condition: object, message: str) -> None:
    if not condition:
        raise ProgressError(message)


def _object(value: object, path: str) -> JsonObject:
    _need(isinstance(value, dict), f"{path} must be an object")
    return cast(JsonObject, value)


def _array(value: object, path: str) -> list[object]:
    _need(isinstance(value, list), f"{path} must be an array")
    return cast(list[object], value)


def _text(value: object, path: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), f"{path} must be text")
    return cast(str, value)


def _iso_date(value: object, path: str) -> str:
    text = _text(value, path)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ProgressError(f"{path} must be an ISO date") from exc
    return text


def _duplicates_rejected(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        _need(key not in result, f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_progress(path: Path) -> JsonObject:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicates_rejected,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProgressError(f"non-finite JSON number: {value}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise ProgressError(
            f"invalid JSON at {exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise ProgressError(f"cannot read {path}: {exc}") from exc
    return _object(value, "document")


def _repo_file(reference: object, repo_root: Path, path: str) -> Path:
    relative = Path(_text(reference, path))
    _need(
        not relative.is_absolute() and ".." not in relative.parts,
        f"{path} must stay inside the repository",
    )
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    _need(resolved.is_relative_to(root), f"{path} escapes the repository")
    _need(resolved.is_file(), f"{path} does not exist: {relative}")
    _need(resolved.stat().st_size > 0, f"{path} must not be empty: {relative}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _understanding_valid(value: object, *, path: str) -> JsonObject:
    understanding = _object(value, path)
    _need(
        understanding.get("version") == 1
        and type(understanding.get("version")) is int,
        f"{path}.version must be the integer 1",
    )
    review_type = _text(
        understanding.get("review_type"),
        f"{path}.review_type",
    )
    _need(
        review_type in UNDERSTANDING_REVIEW_TYPES,
        f"{path}.review_type must be one of "
        f"{sorted(UNDERSTANDING_REVIEW_TYPES)}",
    )
    for field in (
        "agent_impact",
        "decision",
        "invariant",
        "tradeoff",
    ):
        _text(understanding.get(field), f"{path}.{field}")
    proof = _object(understanding.get("proof"), f"{path}.proof")
    proof_kind = _text(proof.get("kind"), f"{path}.proof.kind")
    _need(
        proof_kind in UNDERSTANDING_PROOF_KINDS,
        f"{path}.proof.kind must be one of "
        f"{sorted(UNDERSTANDING_PROOF_KINDS)}",
    )
    _text(proof.get("ref"), f"{path}.proof.ref")
    return understanding


def _passed_structured_understanding(
    value: object,
    *,
    path: str,
) -> bool:
    if not isinstance(value, dict):
        return False
    evidence = cast(JsonObject, value)
    if (
        evidence.get("kind") != "review"
        or evidence.get("result") != "passed"
        or evidence.get("superseded_at_revision") is not None
        or evidence.get("role") == "reopen_reason"
        or "understanding" not in evidence
    ):
        return False
    _understanding_valid(
        evidence["understanding"],
        path=f"{path}.understanding",
    )
    return True


def _matching_understanding_proofs(
    review: JsonObject,
    evidence: Sequence[object],
    *,
    path: str,
) -> list[JsonObject]:
    understanding = _understanding_valid(
        review.get("understanding"),
        path=f"{path}.understanding",
    )
    proof = _object(
        understanding.get("proof"),
        f"{path}.understanding.proof",
    )
    proof_kind = cast(str, proof["kind"])
    proof_ref = cast(str, proof["ref"])
    matches: list[JsonObject] = []
    for value in evidence:
        if not isinstance(value, dict):
            continue
        candidate = cast(JsonObject, value)
        if (
            candidate is review
            or candidate.get("superseded_at_revision") is not None
            or candidate.get("role") == "reopen_reason"
            or candidate.get("ref") != proof_ref
        ):
            continue
        candidate_kind = candidate.get("kind")
        exit_code = candidate.get("exit_code")
        passing_test = (
            candidate_kind == "test"
            and type(exit_code) is int
            and exit_code == 0
        )
        if (
            (proof_kind == "test" and passing_test)
            or (
                proof_kind in {"trace", "metric", "experiment"}
                and (candidate_kind == "artifact" or passing_test)
            )
            or (proof_kind == "diff" and candidate_kind == "commit")
        ):
            matches.append(candidate)
    return matches


def _passed_applied_understanding(
    value: object,
    evidence: Sequence[object],
    *,
    path: str,
) -> bool:
    if not _passed_structured_understanding(value, path=path):
        return False
    review = cast(JsonObject, value)
    return bool(_matching_understanding_proofs(review, evidence, path=path))


def _new_applied_understanding_bundle(
    evidence: Sequence[Evidence],
) -> bool:
    passed_reviews = [
        cast(JsonObject, item)
        for index, item in enumerate(evidence)
        if _passed_structured_understanding(
            item,
            path=f"new_evidence[{index}]",
        )
    ]
    if len(evidence) != 2 or len(passed_reviews) != 1:
        return False
    review = passed_reviews[0]
    return len(
        _matching_understanding_proofs(
            review,
            evidence,
            path="new_understanding_review",
        )
    ) == 1


def _evidence_valid(
    value: object,
    *,
    path: str,
    repo_root: Path,
    repository: RepositoryProbe,
) -> JsonObject:
    evidence = _object(value, path)
    kind = _text(evidence.get("kind"), f"{path}.kind")
    reference = _text(evidence.get("ref"), f"{path}.ref")
    _iso_date(evidence.get("verified_at"), f"{path}.verified_at")
    _need(
        kind in {"artifact", "commit", "test", "review"},
        f"{path}.kind is unsupported: {kind}",
    )
    superseded = evidence.get("superseded_at_revision")
    _need(
        superseded is None or (type(superseded) is int and superseded > 0),
        f"{path}.superseded_at_revision must be a positive integer",
    )

    if kind == "artifact":
        _repo_file(reference, repo_root, f"{path}.ref")
        expected = _text(evidence.get("sha256"), f"{path}.sha256")
        revision = _text(
            evidence.get("git_revision"),
            f"{path}.git_revision",
        )
        _need(SHA256_RE.fullmatch(expected), f"{path}.sha256 is invalid")
        _need(
            COMMIT_RE.fullmatch(revision)
            and repository.commit_oid(revision) is not None,
            f"{path}.git_revision does not resolve: {revision}",
        )
        actual = repository.file_sha256(revision, reference)
        _need(actual is not None, f"{path}.ref does not exist at {revision}")
        _need(
            actual == expected,
            f"{path} hash differs from Git blob at {revision}",
        )
    elif kind == "commit":
        _need(
            COMMIT_RE.fullmatch(reference)
            and repository.commit_oid(reference) is not None,
            f"{path}.ref does not resolve to a commit: {reference}",
        )
    elif kind == "test":
        exit_code = evidence.get("exit_code")
        revision = _text(
            evidence.get("git_revision"),
            f"{path}.git_revision",
        )
        _text(evidence.get("result"), f"{path}.result")
        _need(type(exit_code) is int, f"{path}.exit_code must be an integer")
        _need(
            COMMIT_RE.fullmatch(revision)
            and repository.commit_oid(revision) is not None,
            f"{path}.git_revision does not resolve: {revision}",
        )
    else:
        _need(
            evidence.get("result") in REVIEW_RESULTS,
            f"{path}.result must be one of {sorted(REVIEW_RESULTS)}",
        )
        if "understanding" in evidence:
            _understanding_valid(
                evidence["understanding"],
                path=f"{path}.understanding",
            )
    return evidence


def _successful(evidence: Sequence[object]) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("superseded_at_revision") is None
        and item.get("role") != "reopen_reason"
        and (
            item.get("kind") in {"artifact", "commit"}
            or (item.get("kind") == "test" and item.get("exit_code") == 0)
            or (item.get("kind") == "review" and item.get("result") == "passed")
        )
        for item in evidence
    )


def topic_status(topic: Mapping[str, object]) -> str:
    stages = _object(topic.get("stages"), "topic.stages")
    statuses = [_object(stages[name], f"stage.{name}")["status"] for name in STAGES]
    if all(status == "complete" for status in statuses):
        return "complete"
    if all(status == "pending" for status in statuses):
        return "not_started"
    if "blocked" in statuses:
        return "blocked"
    return "in_progress"


def current_phase(topic: Mapping[str, object]) -> str:
    stages = _object(topic.get("stages"), "topic.stages")
    for name in STAGES:
        if _object(stages[name], f"stage.{name}")["status"] != "complete":
            return name
    return "topic_complete"


def module_status(module: Mapping[str, object]) -> str:
    gate = _object(module.get("gate"), "module.gate")
    if gate.get("status") == "passed":
        return "complete"
    topics = _object(module.get("topics"), "module.topics")
    if any(
        topic_status(_object(topic, "topic")) == "blocked"
        for topic in topics.values()
    ):
        return "blocked"
    return "in_progress" if topics else "not_started"


def _validate_stage(
    name: str,
    value: object,
    *,
    path: str,
    repo_root: Path,
    repository: RepositoryProbe,
) -> None:
    stage = _object(value, path)
    status = _text(stage.get("status"), f"{path}.status")
    _need(status in STATUSES, f"{path}.status is unsupported: {status}")
    evidence = _array(stage.get("evidence"), f"{path}.evidence")
    for index, item in enumerate(evidence):
        _evidence_valid(
            item,
            path=f"{path}.evidence[{index}]",
            repo_root=repo_root,
            repository=repository,
        )
    active = [
        item
        for item in evidence
        if isinstance(item, dict)
        and item.get("superseded_at_revision") is None
    ]
    if name == "understanding_check":
        for index, item in enumerate(evidence):
            if not (
                isinstance(item, dict)
                and item.get("kind") == "review"
                and item.get("result") == "passed"
                and item.get("superseded_at_revision") is None
                and item.get("role") != "reopen_reason"
                and "understanding" in item
            ):
                continue
            _need(
                _matching_understanding_proofs(
                    cast(JsonObject, item),
                    evidence,
                    path=f"{path}.evidence[{index}]",
                ),
                f"{path}.evidence[{index}].understanding.proof must match an "
                "active proof evidence in the same stage",
            )
    if status != "complete":
        return

    _need(_successful(evidence), f"{path}=complete needs successful evidence")
    if name == "theory":
        suffixes = {
            Path(cast(str, item["ref"])).suffix.lower()
            for item in active
            if item.get("kind") == "artifact"
            and isinstance(item.get("ref"), str)
        }
        _need(
            {".md", ".pdf"}.issubset(suffixes),
            f"{path}=complete needs Markdown and PDF artifacts",
        )
    if name == "implementation":
        _need(
            any(item.get("kind") == "artifact" for item in active),
            f"{path}=complete needs an implementation artifact",
        )
    if name == "verification":
        _need(
            any(
                item.get("kind") == "test"
                and item.get("exit_code") == 0
                for item in active
            ),
            f"{path}=complete needs a passing test",
        )
    if name in {"guided_code_tour", "understanding_check"}:
        _need(
            any(
                item.get("kind") == "review"
                and item.get("result") == "passed"
                for item in active
            ),
            f"{path}=complete needs a passed review",
        )
    if name == "notes_finalized":
        suffixes = {
            Path(cast(str, item["ref"])).suffix.lower()
            for item in active
            if item.get("kind") == "artifact"
            and isinstance(item.get("ref"), str)
        }
        _need(
            {".md", ".pdf"}.issubset(suffixes),
            f"{path}=complete needs Markdown and PDF artifacts",
        )


def _validate_topic(
    topic: JsonObject,
    *,
    path: str,
    repo_root: Path,
    repository: RepositoryProbe,
) -> None:
    _text(topic.get("title"), f"{path}.title")
    stages = _object(topic.get("stages"), f"{path}.stages")
    _need(set(stages) == set(STAGES), f"{path}.stages must be {list(STAGES)}")
    for name in STAGES:
        _validate_stage(
            name,
            stages[name],
            path=f"{path}.stages.{name}",
            repo_root=repo_root,
            repository=repository,
        )
    statuses = {
        name: _object(stages[name], f"{path}.stages.{name}")["status"]
        for name in STAGES
    }
    prerequisites = {
        "implementation": ("theory",),
        "verification": ("implementation",),
        "guided_code_tour": ("verification",),
        "understanding_check": ("verification",),
        "notes_finalized": ("guided_code_tour", "understanding_check"),
    }
    for name, required in prerequisites.items():
        if statuses[name] != "pending":
            missing = [
                prerequisite
                for prerequisite in required
                if statuses[prerequisite] != "complete"
            ]
            _need(
                not missing,
                f"{path}.stages.{name} started before prerequisites: {missing}",
            )


def _has_passed_applied_understanding(
    topic: JsonObject,
    *,
    path: str,
) -> bool:
    stages = _object(topic.get("stages"), f"{path}.stages")
    stage = _object(
        stages.get("understanding_check"),
        f"{path}.stages.understanding_check",
    )
    evidence = _array(
        stage.get("evidence"),
        f"{path}.stages.understanding_check.evidence",
    )
    return any(
        _passed_applied_understanding(
            item,
            evidence,
            path=f"{path}.stages.understanding_check.evidence[{index}]",
        )
        for index, item in enumerate(evidence)
    )


def _validate_gate(
    gate: JsonObject,
    *,
    module: JsonObject,
    path: str,
    repo_root: Path,
    repository: RepositoryProbe,
) -> None:
    status = _text(gate.get("status"), f"{path}.status")
    _need(status in {"not_met", "passed"}, f"{path}.status is unsupported")
    criteria = _object(gate.get("criteria"), f"{path}.criteria")
    _need(criteria, f"{path}.criteria must not be empty")
    for criterion_id, value in criteria.items():
        criterion_path = f"{path}.criteria.{criterion_id}"
        _need(
            bool(IDENTIFIER_RE.fullmatch(criterion_id)),
            f"{criterion_path} id must match {IDENTIFIER_RE.pattern}",
        )
        criterion = _object(value, criterion_path)
        _text(criterion.get("description"), f"{criterion_path}.description")
        criterion_status = _text(
            criterion.get("status"),
            f"{criterion_path}.status",
        )
        _need(
            criterion_status in STATUSES,
            f"{criterion_path}.status is unsupported",
        )
        evidence = _array(
            criterion.get("evidence"),
            f"{criterion_path}.evidence",
        )
        for index, item in enumerate(evidence):
            _evidence_valid(
                item,
                path=f"{criterion_path}.evidence[{index}]",
                repo_root=repo_root,
                repository=repository,
            )
        if criterion_status == "complete":
            _need(
                _successful(evidence),
                f"{criterion_path}=complete needs successful evidence",
            )

    policy_field = "understanding_policy_version"
    if status != "passed":
        _need(
            policy_field not in gate,
            f"{path}.{policy_field} must be absent while Gate is not_met",
        )
        return
    policy_version: int | None = None
    if policy_field in gate:
        raw_policy_version = gate[policy_field]
        _need(
            type(raw_policy_version) is int
            and raw_policy_version == UNDERSTANDING_POLICY_VERSION,
            f"{path}.{policy_field} must be the integer "
            f"{UNDERSTANDING_POLICY_VERSION}",
        )
        policy_version = cast(int, raw_policy_version)

    expected = [
        _text(item, f"{path}.expected_topic")
        for item in _array(module.get("expected_topics"), "expected_topics")
    ]
    topics = _object(module.get("topics"), "topics")
    incomplete = [
        topic_id
        for topic_id in expected
        if topic_id not in topics
        or topic_status(_object(topics[topic_id], f"topic.{topic_id}"))
        != "complete"
    ]
    _need(not incomplete, f"{path}=passed has incomplete topics: {incomplete}")
    if policy_version == UNDERSTANDING_POLICY_VERSION:
        topics_without_applied_understanding = [
            topic_id
            for topic_id in expected
            if not _has_passed_applied_understanding(
                _object(topics[topic_id], f"topic.{topic_id}"),
                path=f"topic.{topic_id}",
            )
        ]
        _need(
            not topics_without_applied_understanding,
            f"{path}=passed needs an active passed applied structured "
            "understanding review for topics: "
            f"{topics_without_applied_understanding}",
        )
    incomplete_criteria = [
        criterion_id
        for criterion_id, value in criteria.items()
        if _object(value, f"criterion.{criterion_id}").get("status")
        != "complete"
    ]
    _need(
        not incomplete_criteria,
        f"{path}=passed has incomplete criteria: {incomplete_criteria}",
    )

    for field in ("adr", "evaluation_report"):
        evidence = _evidence_valid(
            gate.get(field),
            path=f"{path}.{field}",
            repo_root=repo_root,
            repository=repository,
        )
        _need(
            evidence.get("kind") == "artifact",
            f"{path}.{field} must be artifact evidence",
        )

    tag = _text(gate.get("git_tag"), f"{path}.git_tag")
    revision = _text(gate.get("git_revision"), f"{path}.git_revision")
    _need(valid_tag(tag), f"{path}.git_tag is invalid")
    revision_oid = repository.commit_oid(revision)
    tag_oid = repository.tag_target_oid(tag)
    _need(revision_oid is not None, f"{path}.git_revision does not resolve")
    _need(tag_oid is not None, f"{path}.git_tag does not exist")
    _need(tag_oid == revision_oid, f"{path}.git_tag points to another commit")
    for field in ("adr", "evaluation_report"):
        artifact = _object(gate[field], f"{path}.{field}")
        artifact_revision = cast(str, artifact["git_revision"])
        _need(
            repository.commit_oid(artifact_revision) == revision_oid,
            f"{path}.{field} is not stored in the tagged commit",
        )


def validate_progress(
    document: Mapping[str, object],
    *,
    repo_root: Path,
    repository: RepositoryProbe | None = None,
) -> None:
    probe = repository or GitRepositoryProbe(repo_root)
    _need(
        document.get("schema_version") == SCHEMA_VERSION,
        f"schema_version must be {SCHEMA_VERSION}",
    )
    revision = document.get("revision")
    _need(type(revision) is int and cast(int, revision) > 0, "invalid revision")
    updated_at = _iso_date(document.get("updated_at"), "updated_at")

    track = _object(document.get("track"), "track")
    _text(track.get("id"), "track.id")
    _text(track.get("title"), "track.title")
    _repo_file(track.get("plan"), repo_root, "track.plan")

    modules = _object(document.get("modules"), "modules")
    _need(modules, "modules must not be empty")
    _need(
        all(re.fullmatch(r"\d{2}", module_id) for module_id in modules),
        "module ids must be two digits",
    )
    module_ids = sorted(modules, key=int)
    _need(
        module_ids == [f"{number:02d}" for number in range(1, len(module_ids) + 1)],
        "modules must form a contiguous sequence from 01",
    )
    for module_id, value in modules.items():
        module_path = f"modules.{module_id}"
        module = _object(value, module_path)
        _text(module.get("title"), f"{module_path}.title")
        expected = [
            _text(item, f"{module_path}.expected_topics")
            for item in _array(
                module.get("expected_topics"),
                f"{module_path}.expected_topics",
            )
        ]
        _need(expected, f"{module_path}.expected_topics must not be empty")
        _need(
            len(expected) == len(set(expected)),
            f"{module_path}.expected_topics has duplicates",
        )
        topic_prefix = str(int(module_id))
        _need(
            expected
            == [
                f"{topic_prefix}.{number}"
                for number in range(1, len(expected) + 1)
            ],
            f"{module_path}.expected_topics must be a contiguous "
            f"{topic_prefix}.1..{topic_prefix}.N sequence",
        )
        topics = _object(module.get("topics"), f"{module_path}.topics")
        for topic_id, topic_value in topics.items():
            _need(
                topic_id in expected,
                f"{module_path}.topics.{topic_id} is not expected",
            )
            _validate_topic(
                _object(topic_value, f"{module_path}.topics.{topic_id}"),
                path=f"{module_path}.topics.{topic_id}",
                repo_root=repo_root,
                repository=probe,
            )
        existing_indexes = sorted(expected.index(topic_id) for topic_id in topics)
        _need(
            existing_indexes == list(range(len(existing_indexes))),
            f"{module_path}.topics must form a contiguous expected prefix",
        )
        _validate_gate(
            _object(module.get("gate"), f"{module_path}.gate"),
            module=module,
            path=f"{module_path}.gate",
            repo_root=repo_root,
            repository=probe,
        )

    current = _object(document.get("current"), "current")
    current_module = _text(current.get("module"), "current.module")
    current_topic = _text(current.get("topic"), "current.topic")
    _need(current_module in modules, "current.module does not exist")
    _need(current_module == module_ids[-1], "current.module must be the latest")
    for previous_module_id in module_ids[:-1]:
        previous_module = _object(
            modules[previous_module_id],
            f"modules.{previous_module_id}",
        )
        previous_gate = _object(
            previous_module.get("gate"),
            f"modules.{previous_module_id}.gate",
        )
        _need(
            previous_gate.get("status") == "passed",
            f"module {previous_module_id} Gate must pass before the next module",
        )
    module = _object(modules[current_module], f"modules.{current_module}")
    topics = _object(module.get("topics"), f"modules.{current_module}.topics")
    _need(current_topic in topics, "current.topic does not exist")
    expected = cast(list[object], module["expected_topics"])
    _need(
        expected.index(current_topic) == len(topics) - 1,
        "current.topic must be the latest started topic",
    )

    history = _array(document.get("history"), "history")
    _need(history, "history must not be empty")
    previous_at: str | None = None
    for index, value in enumerate(history, start=1):
        event = _object(value, f"history[{index - 1}]")
        _need(event.get("revision") == index, "history revisions are not append-only")
        event_at = _iso_date(event.get("at"), f"history[{index - 1}].at")
        _need(
            previous_at is None or event_at >= previous_at,
            "history dates must be monotonic",
        )
        previous_at = event_at
        for field in ("actor", "action", "target", "summary"):
            _text(event.get(field), f"history[{index - 1}].{field}")
    _need(len(history) == revision, "history length must equal revision")
    last = _object(history[-1], "history[-1]")
    _need(last.get("at") == updated_at, "updated_at differs from latest event")


def _append_history(
    document: JsonObject,
    *,
    at: str,
    actor: str,
    action: str,
    target: str,
    summary: str,
) -> None:
    revision = cast(int, document["revision"]) + 1
    document["revision"] = revision
    document["updated_at"] = at
    cast(list[object], document["history"]).append(
        {
            "revision": revision,
            "at": at,
            "actor": actor,
            "action": action,
            "target": target,
            "summary": summary,
        }
    )


def _transition(
    old: str,
    new: str,
    *,
    reopen: bool,
    evidence: Sequence[Evidence],
    target: str,
) -> None:
    _need(new in STATUSES, f"unsupported status: {new}")
    if old == "complete" and new != "complete":
        _need(reopen, f"{target} is complete; use --reopen")
        _need(new in {"in_progress", "blocked"}, f"cannot reopen to {new}")
        _need(
            any(item.get("kind") == "review" for item in evidence),
            "reopen requires --review with a reason",
        )
    else:
        _need(not reopen, "--reopen applies only to a complete item")
        _need(
            new != "pending" or old == "pending",
            f"{target} cannot regress to pending",
        )


def _get_topic(
    document: JsonObject,
    module_id: str,
    topic_id: str,
) -> tuple[JsonObject, JsonObject]:
    modules = _object(document["modules"], "modules")
    _need(module_id in modules, f"unknown module {module_id}")
    module = _object(modules[module_id], f"module.{module_id}")
    topics = _object(module["topics"], "topics")
    _need(topic_id in topics, f"unknown topic {topic_id}")
    return module, _object(topics[topic_id], f"topic.{topic_id}")


def record_stage(
    document: JsonObject,
    *,
    module_id: str,
    topic_id: str,
    stage_name: str,
    new_status: str,
    evidence: Sequence[Evidence],
    at: str,
    actor: str,
    reopen: bool = False,
) -> None:
    current = _object(document["current"], "current")
    module, topic = _get_topic(document, module_id, topic_id)
    gate = _object(module.get("gate"), f"module.{module_id}.gate")
    _need(
        gate.get("status") != "passed",
        f"module {module_id} stages cannot change after Gate passed",
    )
    stages = _object(topic["stages"], "stages")
    _need(stage_name in stages, f"unknown stage {stage_name}")
    stage = _object(stages[stage_name], f"stage.{stage_name}")
    old = cast(str, stage["status"])
    has_new_applied_understanding_bundle = _new_applied_understanding_bundle(
        evidence
    )
    non_current_understanding_review = (
        stage_name == "understanding_check"
        and old == "complete"
        and new_status == "complete"
        and not reopen
        and has_new_applied_understanding_bundle
    )
    _need(
        (current["module"], current["topic"]) == (module_id, topic_id)
        or non_current_understanding_review,
        "only the current topic may be updated, except for a new structured "
        "review of a completed understanding_check",
    )
    _transition(
        old,
        new_status,
        reopen=reopen,
        evidence=evidence,
        target=stage_name,
    )
    if stage_name == "understanding_check" and new_status == "complete":
        _need(
            has_new_applied_understanding_bundle,
            "recording understanding_check=complete needs exactly one newly "
            "supplied passed structured review and exactly one matching proof "
            "evidence",
        )
    stored = _array(stage["evidence"], f"stage.{stage_name}.evidence")
    invalidated: list[str] = []
    if reopen:
        next_revision = cast(int, document["revision"]) + 1
        _supersede(stored, next_revision)
        for item in evidence:
            if item.get("kind") == "review":
                item["role"] = "reopen_reason"
        topic_stages = _object(topic["stages"], "stages")
        for dependent in _downstream_stages(stage_name):
            dependent_stage = _object(
                topic_stages[dependent],
                f"stage.{dependent}",
            )
            dependent_evidence = _array(
                dependent_stage["evidence"],
                f"stage.{dependent}.evidence",
            )
            if dependent_stage["status"] != "pending" or dependent_evidence:
                invalidated.append(dependent)
            _supersede(dependent_evidence, next_revision)
            dependent_stage["status"] = "pending"
    stored.extend(evidence)
    if new_status == "complete":
        _need(_successful(stored), f"{stage_name}=complete needs evidence")
    stage["status"] = new_status
    _append_history(
        document,
        at=at,
        actor=actor,
        action="stage_recorded",
        target=f"module:{module_id}/topic:{topic_id}/stage:{stage_name}",
        summary=(
            f"{old} -> {new_status}; added_evidence={len(evidence)}; "
            f"invalidated={invalidated}"
        ),
    )


def _supersede(evidence: Sequence[object], revision: int) -> None:
    for item in evidence:
        if isinstance(item, dict) and item.get("superseded_at_revision") is None:
            item["superseded_at_revision"] = revision


def _downstream_stages(stage_name: str) -> tuple[str, ...]:
    downstream = {
        "theory": (
            "implementation",
            "verification",
            "guided_code_tour",
            "understanding_check",
            "notes_finalized",
        ),
        "implementation": (
            "verification",
            "guided_code_tour",
            "understanding_check",
            "notes_finalized",
        ),
        "verification": (
            "guided_code_tour",
            "understanding_check",
            "notes_finalized",
        ),
        "guided_code_tour": ("notes_finalized",),
        "understanding_check": ("notes_finalized",),
        "notes_finalized": (),
    }
    return downstream[stage_name]


def start_topic(
    document: JsonObject,
    *,
    module_id: str,
    topic_id: str,
    title: str,
    at: str,
    actor: str,
) -> None:
    current = _object(document["current"], "current")
    _need(current["module"] == module_id, "cannot skip the current module")
    module, current_topic = _get_topic(
        document,
        module_id,
        cast(str, current["topic"]),
    )
    _need(
        topic_status(current_topic) == "complete",
        "current topic is not complete",
    )
    expected = _array(module["expected_topics"], "expected_topics")
    topics = _object(module["topics"], "topics")
    current_topic_id = cast(str, current["topic"])
    current_index = expected.index(current_topic_id)
    _need(current_index + 1 < len(expected), "module has no next topic")
    _need(
        topic_id == expected[current_index + 1],
        f"next topic must be {expected[current_index + 1]}",
    )
    _need(topic_id not in topics, f"topic {topic_id} already exists")
    topics[topic_id] = _new_topic(title)
    current.update({"module": module_id, "topic": topic_id})
    _append_history(
        document,
        at=at,
        actor=actor,
        action="topic_started",
        target=f"module:{module_id}/topic:{topic_id}",
        summary=f"Начата тема: {title}",
    )


def _new_topic(title: str) -> JsonObject:
    return {
        "title": title,
        "stages": {
            name: {
                "status": "in_progress" if name == "theory" else "pending",
                "evidence": [],
            }
            for name in STAGES
        },
    }


def start_module(
    document: JsonObject,
    *,
    module_id: str,
    title: str,
    expected_topics: Sequence[str],
    first_topic_title: str,
    criteria: Mapping[str, str],
    at: str,
    actor: str,
) -> None:
    modules = _object(document["modules"], "modules")
    current = _object(document["current"], "current")
    current_module_id = cast(str, current["module"])
    current_module = _object(modules[current_module_id], "current module")
    current_gate = _object(current_module["gate"], "current module Gate")
    _need(current_gate["status"] == "passed", "current module Gate is not passed")
    _need(module_id not in modules, f"module {module_id} already exists")
    _need(
        re.fullmatch(r"\d{2}", module_id)
        and int(module_id) == int(current_module_id) + 1,
        f"next module must be {int(current_module_id) + 1:02d}",
    )
    _need(expected_topics, "expected_topics must not be empty")
    _need(
        len(expected_topics) == len(set(expected_topics)),
        "expected_topics contains duplicates",
    )
    _need(criteria, "Gate criteria must not be empty")
    first_topic_id = expected_topics[0]
    modules[module_id] = {
        "title": title,
        "expected_topics": list(expected_topics),
        "topics": {first_topic_id: _new_topic(first_topic_title)},
        "gate": {
            "status": "not_met",
            "criteria": {
                criterion_id: {
                    "description": description,
                    "status": "pending",
                    "evidence": [],
                }
                for criterion_id, description in criteria.items()
            },
            "adr": None,
            "evaluation_report": None,
            "git_tag": None,
            "git_revision": None,
        },
    }
    current.update({"module": module_id, "topic": first_topic_id})
    _append_history(
        document,
        at=at,
        actor=actor,
        action="module_started",
        target=f"module:{module_id}/topic:{first_topic_id}",
        summary=f"Начат модуль: {title}",
    )


def record_criterion(
    document: JsonObject,
    *,
    module_id: str,
    criterion_id: str,
    new_status: str,
    evidence: Sequence[Evidence],
    at: str,
    actor: str,
    reopen: bool = False,
) -> None:
    modules = _object(document["modules"], "modules")
    _need(module_id in modules, f"unknown module {module_id}")
    module = _object(modules[module_id], f"module.{module_id}")
    gate = _object(module["gate"], "gate")
    _need(gate["status"] != "passed", "passed Gate is immutable")
    criteria = _object(gate["criteria"], "criteria")
    _need(criterion_id in criteria, f"unknown criterion {criterion_id}")
    criterion = _object(criteria[criterion_id], f"criterion.{criterion_id}")
    old = cast(str, criterion["status"])
    _transition(
        old,
        new_status,
        reopen=reopen,
        evidence=evidence,
        target=criterion_id,
    )
    stored = _array(criterion["evidence"], "criterion.evidence")
    if reopen:
        _supersede(stored, cast(int, document["revision"]) + 1)
        for item in evidence:
            if item.get("kind") == "review":
                item["role"] = "reopen_reason"
    stored.extend(evidence)
    if new_status == "complete":
        _need(_successful(stored), f"{criterion_id}=complete needs evidence")
    criterion["status"] = new_status
    _append_history(
        document,
        at=at,
        actor=actor,
        action="criterion_recorded",
        target=f"module:{module_id}/criterion:{criterion_id}",
        summary=f"{old} -> {new_status}; added_evidence={len(evidence)}",
    )


def pass_gate(
    document: JsonObject,
    *,
    module_id: str,
    adr: Evidence,
    evaluation_report: Evidence,
    git_tag: str,
    git_revision: str,
    at: str,
    actor: str,
) -> None:
    modules = _object(document["modules"], "modules")
    _need(module_id in modules, f"unknown module {module_id}")
    module = _object(modules[module_id], f"module.{module_id}")
    gate = _object(module["gate"], "gate")
    _need(gate["status"] != "passed", "Gate is already passed")
    expected = _array(module["expected_topics"], "expected_topics")
    topics = _object(module["topics"], "topics")
    completed_without_applied_understanding: list[str] = []
    for value in expected:
        topic_id = _text(value, "expected_topic")
        if topic_id not in topics:
            continue
        topic = _object(topics[topic_id], f"topic.{topic_id}")
        if topic_status(topic) == "complete" and not (
            _has_passed_applied_understanding(
                topic,
                path=f"topic.{topic_id}",
            )
        ):
            completed_without_applied_understanding.append(topic_id)
    _need(
        not completed_without_applied_understanding,
        "Gate needs an active passed applied structured understanding review "
        f"for completed topics: {completed_without_applied_understanding}",
    )
    gate.update(
        {
            "status": "passed",
            "adr": adr,
            "evaluation_report": evaluation_report,
            "git_tag": git_tag,
            "git_revision": git_revision,
            "understanding_policy_version": UNDERSTANDING_POLICY_VERSION,
        }
    )
    _append_history(
        document,
        at=at,
        actor=actor,
        action="gate_passed",
        target=f"module:{module_id}/gate",
        summary=f"Gate подтверждён tag={git_tag} revision={git_revision}",
    )


def build_evidence(
    *,
    artifacts: Sequence[str],
    commits: Sequence[str],
    test_command: str | None,
    test_result: str | None,
    test_exit_code: int | None,
    reviews: Sequence[str],
    review_result: str,
    verified_at: str,
    repo_root: Path,
    repository: RepositoryProbe,
) -> list[Evidence]:
    _iso_date(verified_at, "--verified-at")
    test_values = (test_command, test_result, test_exit_code)
    _need(
        not any(value is not None for value in test_values)
        or all(value is not None for value in test_values),
        "test command, result and exit code must be provided together",
    )
    evidence: list[Evidence] = []
    head = repository.head_oid()
    for reference in artifacts:
        artifact = _repo_file(reference, repo_root, "--artifact")
        _need(head is not None, "cannot resolve HEAD for artifact evidence")
        committed_hash = repository.file_sha256(head, reference)
        _need(
            committed_hash is not None,
            f"artifact is not committed at HEAD: {reference}",
        )
        _need(
            committed_hash == _sha256(artifact),
            f"commit artifact before recording evidence: {reference}",
        )
        evidence.append(
            {
                "kind": "artifact",
                "ref": reference,
                "sha256": committed_hash,
                "git_revision": head,
                "verified_at": verified_at,
            }
        )
    for revision in commits:
        _need(
            COMMIT_RE.fullmatch(revision)
            and repository.commit_oid(revision) is not None,
            f"commit does not resolve: {revision}",
        )
        evidence.append(
            {
                "kind": "commit",
                "ref": revision,
                "verified_at": verified_at,
            }
        )
    if test_command is not None:
        _need(head is not None, "cannot resolve HEAD for test evidence")
        evidence.append(
            {
                "kind": "test",
                "ref": test_command,
                "result": test_result,
                "exit_code": test_exit_code,
                "git_revision": head,
                "verified_at": verified_at,
            }
        )
    evidence.extend(
        {
            "kind": "review",
            "ref": review,
            "result": review_result,
            "verified_at": verified_at,
        }
        for review in reviews
    )
    return evidence


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    lock_id = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
    lock_path = Path(tempfile.gettempdir()) / f"learning-{lock_id}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write(path: Path, document: Mapping[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(document, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def mutate_progress(
    *,
    progress_path: Path,
    repo_root: Path,
    expected_revision: int,
    mutation: Callable[[JsonObject], None],
    repository: RepositoryProbe | None = None,
) -> JsonObject:
    probe = repository or GitRepositoryProbe(repo_root)
    with _lock(progress_path):
        document = load_progress(progress_path)
        validate_progress(document, repo_root=repo_root, repository=probe)
        _need(
            document["revision"] == expected_revision,
            f"revision conflict: expected {expected_revision}, "
            f"actual {document['revision']}",
        )
        mutation(document)
        validate_progress(document, repo_root=repo_root, repository=probe)
        _write(progress_path, document)
    return document


def render_progress(document: Mapping[str, object]) -> str:
    current = _object(document["current"], "current")
    modules = _object(document["modules"], "modules")
    module = _object(modules[cast(str, current["module"])], "module")
    topics = _object(module["topics"], "topics")
    topic = _object(topics[cast(str, current["topic"])], "topic")
    stages = _object(topic["stages"], "stages")
    gate = _object(module["gate"], "gate")
    criteria = _object(gate["criteria"], "criteria")
    completed_stages = sum(
        _object(stage, "stage")["status"] == "complete"
        for stage in stages.values()
    )
    completed_criteria = sum(
        _object(criterion, "criterion")["status"] == "complete"
        for criterion in criteria.values()
    )
    glyph = {"pending": "○", "in_progress": "→", "blocked": "!", "complete": "✓"}
    lines = [
        _text(_object(document["track"], "track")["title"], "track.title"),
        f"revision: {document['revision']} (updated {document['updated_at']})",
        (
            f"current: module {current['module']} / topic {current['topic']} "
            f"/ {current_phase(topic)}"
        ),
        (
            f"module: {module_status(module)}; Gate: {gate['status']} "
            f"({completed_criteria}/{len(criteria)} criteria)"
        ),
        (
            f"topic: {topic_status(topic)} "
            f"({completed_stages}/{len(STAGES)} stages)"
        ),
    ]
    for name in STAGES:
        stage = _object(stages[name], f"stage.{name}")
        status = cast(str, stage["status"])
        lines.append(
            f"  {glyph[status]} {name}: {status} "
            f"(evidence={len(_array(stage['evidence'], 'evidence'))})"
        )
    return "\n".join(lines)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--actor", default="ai-tutor")
    parser.add_argument("--at", default=date.today().isoformat())


def _evidence_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--commit", action="append", default=[])
    parser.add_argument("--test-command")
    parser.add_argument("--test-result")
    parser.add_argument("--test-exit-code", type=int)
    parser.add_argument("--review", action="append", default=[])
    parser.add_argument(
        "--review-result",
        choices=sorted(REVIEW_RESULTS),
        default="partial",
    )
    parser.add_argument("--verified-at", default=date.today().isoformat())


def _understanding_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--review-type",
        choices=sorted(UNDERSTANDING_REVIEW_TYPES),
    )
    parser.add_argument("--agent-impact")
    parser.add_argument("--decision")
    parser.add_argument("--invariant")
    parser.add_argument("--tradeoff")
    parser.add_argument(
        "--proof-kind",
        choices=sorted(UNDERSTANDING_PROOF_KINDS),
    )
    parser.add_argument("--proof")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--file", type=Path, default=DEFAULT_PROGRESS_PATH)
    root.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    commands.add_parser("validate")

    start = commands.add_parser("start-topic")
    _common(start)
    start.add_argument("--module", required=True)
    start.add_argument("--topic", required=True)
    start.add_argument("--title", required=True)

    start_module_parser = commands.add_parser("start-module")
    _common(start_module_parser)
    start_module_parser.add_argument("--module", required=True)
    start_module_parser.add_argument("--title", required=True)
    start_module_parser.add_argument(
        "--expected-topic",
        action="append",
        required=True,
    )
    start_module_parser.add_argument("--first-topic-title", required=True)
    start_module_parser.add_argument(
        "--criterion",
        action="append",
        type=_criterion_argument,
        required=True,
        metavar="ID=DESCRIPTION",
    )

    stage = commands.add_parser("record-stage")
    _common(stage)
    _evidence_args(stage)
    _understanding_args(stage)
    stage.add_argument("--module", required=True)
    stage.add_argument("--topic", required=True)
    stage.add_argument("--stage", choices=STAGES, required=True)
    stage.add_argument("--status", choices=sorted(STATUSES), required=True)
    stage.add_argument("--reopen", action="store_true")

    criterion = commands.add_parser("record-criterion")
    _common(criterion)
    _evidence_args(criterion)
    criterion.add_argument("--module", required=True)
    criterion.add_argument("--criterion", required=True)
    criterion.add_argument("--status", choices=sorted(STATUSES), required=True)
    criterion.add_argument("--reopen", action="store_true")

    gate = commands.add_parser("pass-gate")
    _common(gate)
    gate.add_argument("--module", required=True)
    gate.add_argument("--adr", required=True)
    gate.add_argument("--evaluation-report", required=True)
    gate.add_argument("--git-tag", required=True)
    gate.add_argument("--git-revision", required=True)
    return root


def _criterion_argument(value: str) -> tuple[str, str]:
    criterion_id, separator, description = value.partition("=")
    if not separator or not criterion_id.strip() or not description.strip():
        raise argparse.ArgumentTypeError(
            "criterion must have the form ID=DESCRIPTION"
        )
    return criterion_id.strip(), description.strip()


def _from_args(
    arguments: argparse.Namespace,
    repo_root: Path,
    repository: RepositoryProbe,
) -> list[Evidence]:
    evidence = build_evidence(
        artifacts=arguments.artifact,
        commits=arguments.commit,
        test_command=arguments.test_command,
        test_result=arguments.test_result,
        test_exit_code=arguments.test_exit_code,
        reviews=arguments.review,
        review_result=arguments.review_result,
        verified_at=arguments.verified_at,
        repo_root=repo_root,
        repository=repository,
    )
    understanding_values = (
        getattr(arguments, "review_type", None),
        getattr(arguments, "agent_impact", None),
        getattr(arguments, "decision", None),
        getattr(arguments, "invariant", None),
        getattr(arguments, "tradeoff", None),
        getattr(arguments, "proof_kind", None),
        getattr(arguments, "proof", None),
    )
    if not any(value is not None for value in understanding_values):
        return evidence
    _need(
        all(value is not None for value in understanding_values),
        "review type, Agent impact, decision, invariant, tradeoff, proof kind "
        "and proof must be provided together",
    )
    _need(
        arguments.stage == "understanding_check",
        "structured understanding review applies only to understanding_check",
    )
    reviews = [item for item in evidence if item.get("kind") == "review"]
    _need(
        len(reviews) == 1,
        "structured understanding review requires exactly one --review",
    )
    reviews[0]["understanding"] = {
        "version": 1,
        "review_type": arguments.review_type,
        "agent_impact": arguments.agent_impact,
        "decision": arguments.decision,
        "invariant": arguments.invariant,
        "tradeoff": arguments.tradeoff,
        "proof": {
            "kind": arguments.proof_kind,
            "ref": arguments.proof,
        },
    }
    return evidence


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = parser().parse_args(argv)
    path = arguments.file.resolve()
    repo_root = arguments.repo_root.resolve()
    repository = GitRepositoryProbe(repo_root)
    try:
        if arguments.command in {"show", "validate"}:
            document = load_progress(path)
            validate_progress(
                document,
                repo_root=repo_root,
                repository=repository,
            )
            if arguments.command == "show":
                print(render_progress(document), file=stdout)
            else:
                print(f"OK: revision={document['revision']} file={path}", file=stdout)
            return 0

        if arguments.command == "start-topic":
            mutation = lambda state: start_topic(
                state,
                module_id=arguments.module,
                topic_id=arguments.topic,
                title=arguments.title,
                at=arguments.at,
                actor=arguments.actor,
            )
        elif arguments.command == "start-module":
            criteria = dict(arguments.criterion)
            _need(
                len(criteria) == len(arguments.criterion),
                "criterion ids contain duplicates",
            )
            mutation = lambda state: start_module(
                state,
                module_id=arguments.module,
                title=arguments.title,
                expected_topics=arguments.expected_topic,
                first_topic_title=arguments.first_topic_title,
                criteria=criteria,
                at=arguments.at,
                actor=arguments.actor,
            )
        elif arguments.command == "record-stage":
            evidence = _from_args(arguments, repo_root, repository)
            mutation = lambda state: record_stage(
                state,
                module_id=arguments.module,
                topic_id=arguments.topic,
                stage_name=arguments.stage,
                new_status=arguments.status,
                evidence=evidence,
                at=arguments.at,
                actor=arguments.actor,
                reopen=arguments.reopen,
            )
        elif arguments.command == "record-criterion":
            evidence = _from_args(arguments, repo_root, repository)
            mutation = lambda state: record_criterion(
                state,
                module_id=arguments.module,
                criterion_id=arguments.criterion,
                new_status=arguments.status,
                evidence=evidence,
                at=arguments.at,
                actor=arguments.actor,
                reopen=arguments.reopen,
            )
        else:
            adr, evaluation = (
                build_evidence(
                    artifacts=[reference],
                    commits=[],
                    test_command=None,
                    test_result=None,
                    test_exit_code=None,
                    reviews=[],
                    review_result="partial",
                    verified_at=arguments.at,
                    repo_root=repo_root,
                    repository=repository,
                )[0]
                for reference in (arguments.adr, arguments.evaluation_report)
            )
            mutation = lambda state: pass_gate(
                state,
                module_id=arguments.module,
                adr=adr,
                evaluation_report=evaluation,
                git_tag=arguments.git_tag,
                git_revision=arguments.git_revision,
                at=arguments.at,
                actor=arguments.actor,
            )

        document = mutate_progress(
            progress_path=path,
            repo_root=repo_root,
            expected_revision=arguments.expected_revision,
            mutation=mutation,
            repository=repository,
        )
    except (OSError, ProgressError) as exc:
        print(f"ERROR: {exc}", file=stderr)
        return 2
    print(render_progress(document), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
