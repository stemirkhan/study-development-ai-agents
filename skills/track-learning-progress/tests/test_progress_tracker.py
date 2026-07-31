from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import progress_tracker as tracker  # noqa: E402

OID_A = "a" * 40
OID_B = "b" * 40
TODAY = "2026-07-27"


@dataclass
class FakeRepository:
    commits: dict[str, str] = field(
        default_factory=lambda: {OID_A: OID_A, OID_A[:7]: OID_A}
    )
    head: str = OID_A
    tags: dict[str, str] = field(default_factory=dict)
    file_hashes: dict[tuple[str, str], str] = field(default_factory=dict)

    def commit_oid(self, revision: str) -> str | None:
        return self.commits.get(revision)

    def head_oid(self) -> str | None:
        return self.head

    def file_sha256(self, revision: str, path: str) -> str | None:
        oid = self.commit_oid(revision)
        return self.file_hashes.get((oid, path)) if oid is not None else None

    def tag_target_oid(self, tag: str) -> str | None:
        return self.tags.get(tag)


def _write(repo_root: Path, relative: str, content: bytes) -> Path:
    path = repo_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _artifact(repo_root: Path, relative: str) -> tracker.Evidence:
    path = repo_root / relative
    return {
        "kind": "artifact",
        "ref": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "git_revision": OID_A,
        "verified_at": TODAY,
    }


def _probe(
    repo_root: Path,
    *,
    commits: dict[str, str] | None = None,
    head: str = OID_A,
    tags: dict[str, str] | None = None,
) -> FakeRepository:
    resolved_commits = commits or {OID_A: OID_A, OID_A[:7]: OID_A}
    file_hashes = {
        (OID_A, str(path.relative_to(repo_root))): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in repo_root.rglob("*")
        if path.is_file()
    }
    return FakeRepository(
        commits=resolved_commits,
        head=head,
        tags=tags or {},
        file_hashes=file_hashes,
    )


def _review(text: str, result: str = "passed") -> tracker.Evidence:
    return {
        "kind": "review",
        "ref": text,
        "result": result,
        "verified_at": TODAY,
    }


def _structured_understanding_review(
    *,
    result: str = "passed",
    proof_kind: str = "test",
    proof_ref: str = "pytest -q",
) -> tracker.Evidence:
    evidence = _review("Разобрана граница решения Agent", result=result)
    evidence["understanding"] = {
        "version": 1,
        "review_type": "failure_analysis",
        "agent_impact": "Повтор меняет состояние Agent и риск второго tool call.",
        "decision": "Не повторять неоднозначно завершившийся побочный эффект.",
        "invariant": "Один idempotency key приводит не более чем к одному эффекту.",
        "tradeoff": "Ручная сверка медленнее автоматического повтора.",
        "proof": {
            "kind": proof_kind,
            "ref": proof_ref,
        },
    }
    return evidence


def _test_evidence(
    exit_code: int = 0,
    *,
    ref: str = "pytest -q",
) -> tracker.Evidence:
    return {
        "kind": "test",
        "ref": ref,
        "result": "1 passed" if exit_code == 0 else "1 failed",
        "exit_code": exit_code,
        "git_revision": OID_A,
        "verified_at": TODAY,
    }


def _commit_evidence(ref: str = OID_A[:7]) -> tracker.Evidence:
    return {
        "kind": "commit",
        "ref": ref,
        "verified_at": TODAY,
    }


def _applied_understanding_evidence(
    *,
    result: str = "passed",
) -> list[tracker.Evidence]:
    proof = _test_evidence()
    return [
        _structured_understanding_review(
            result=result,
            proof_kind="test",
            proof_ref=cast(str, proof["ref"]),
        ),
        proof,
    ]


def _document(
    repo_root: Path,
    *,
    expected_topics: list[str] | None = None,
) -> tracker.JsonObject:
    _write(repo_root, "docs/plan.md", b"plan")
    _write(repo_root, "notes/topic.md", b"note")
    _write(repo_root, "notes/topic.pdf", b"pdf")
    _write(repo_root, "lessons/topic.py", b"code")
    stages: tracker.JsonObject = {
        stage: {"status": "pending", "evidence": []}
        for stage in tracker.STAGES
    }
    cast(tracker.JsonObject, stages["theory"])["status"] = "in_progress"
    return {
        "schema_version": 1,
        "revision": 1,
        "updated_at": TODAY,
        "track": {
            "id": "test-track",
            "title": "Test Track",
            "plan": "docs/plan.md",
        },
        "current": {
            "module": "01",
            "topic": "1.1",
        },
        "modules": {
            "01": {
                "title": "Module",
                "expected_topics": expected_topics or ["1.1"],
                "topics": {
                    "1.1": {
                        "title": "Topic",
                        "stages": stages,
                    }
                },
                "gate": {
                    "status": "not_met",
                    "criteria": {
                        "criterion": {
                            "description": "Observable criterion",
                            "status": "pending",
                            "evidence": [],
                        }
                    },
                    "adr": None,
                    "evaluation_report": None,
                    "git_tag": None,
                    "git_revision": None,
                },
            }
        },
        "history": [
            {
                "revision": 1,
                "at": TODAY,
                "actor": "test",
                "action": "tracker_initialized",
                "target": "module:01/topic:1.1",
                "summary": "Initialized",
            }
        ],
    }


def _topic(document: tracker.JsonObject) -> tracker.JsonObject:
    modules = cast(tracker.JsonObject, document["modules"])
    module = cast(tracker.JsonObject, modules["01"])
    topics = cast(tracker.JsonObject, module["topics"])
    return cast(tracker.JsonObject, topics["1.1"])


def _module(document: tracker.JsonObject) -> tracker.JsonObject:
    modules = cast(tracker.JsonObject, document["modules"])
    return cast(tracker.JsonObject, modules["01"])


def _complete_topic(document: tracker.JsonObject, repo_root: Path) -> None:
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    stage_evidence: dict[str, list[tracker.Evidence]] = {
        "theory": [
            _artifact(repo_root, "notes/topic.md"),
            _artifact(repo_root, "notes/topic.pdf"),
        ],
        "implementation": [_artifact(repo_root, "lessons/topic.py")],
        "verification": [_test_evidence()],
        "guided_code_tour": [_review("Code tour passed")],
        "understanding_check": [_review("Understanding passed")],
        "notes_finalized": [
            _artifact(repo_root, "notes/topic.md"),
            _artifact(repo_root, "notes/topic.pdf"),
        ],
    }
    for stage_name, evidence in stage_evidence.items():
        stage = cast(tracker.JsonObject, stages[stage_name])
        stage["status"] = "complete"
        stage["evidence"] = evidence


def _complete_through_verification(
    document: tracker.JsonObject,
    repo_root: Path,
) -> None:
    stages = cast(tracker.JsonObject, _topic(document)["stages"])
    evidence_by_stage: dict[str, list[tracker.Evidence]] = {
        "theory": [
            _artifact(repo_root, "notes/topic.md"),
            _artifact(repo_root, "notes/topic.pdf"),
        ],
        "implementation": [_artifact(repo_root, "lessons/topic.py")],
        "verification": [_test_evidence()],
    }
    for stage_name, evidence in evidence_by_stage.items():
        stage = cast(tracker.JsonObject, stages[stage_name])
        stage["status"] = "complete"
        stage["evidence"] = evidence


def _add_structured_understanding_review(
    document: tracker.JsonObject,
    *,
    topic_id: str = "1.1",
) -> None:
    tracker.record_stage(
        document,
        module_id="01",
        topic_id=topic_id,
        stage_name="understanding_check",
        new_status="complete",
        evidence=_applied_understanding_evidence(),
        at=TODAY,
        actor="test",
    )


def test_repository_progress_file_is_valid() -> None:
    document = tracker.load_progress(REPO_ROOT / "progress" / "progress.json")

    tracker.validate_progress(document, repo_root=REPO_ROOT)


def test_artifact_hash_must_match_committed_blob(tmp_path: Path) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    theory = cast(tracker.JsonObject, stages["theory"])
    evidence = _artifact(tmp_path, "notes/topic.md")
    evidence["sha256"] = "0" * 64
    theory["evidence"] = [evidence]

    with pytest.raises(tracker.ProgressError, match="hash differs from Git blob"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_working_tree_change_does_not_invalidate_historical_artifact(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    theory = cast(tracker.JsonObject, stages["theory"])
    theory["evidence"] = [_artifact(tmp_path, "notes/topic.md")]
    probe = _probe(tmp_path)
    _write(tmp_path, "notes/topic.md", b"changed")

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=probe,
    )


def test_complete_verification_requires_passing_test(tmp_path: Path) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    verification = cast(tracker.JsonObject, stages["verification"])
    verification["status"] = "complete"
    verification["evidence"] = [_test_evidence(exit_code=1)]
    with pytest.raises(tracker.ProgressError, match="needs successful evidence"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_complete_theory_requires_markdown_and_pdf(tmp_path: Path) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    theory = cast(tracker.JsonObject, stages["theory"])
    theory["status"] = "complete"
    theory["evidence"] = [_artifact(tmp_path, "notes/topic.md")]

    with pytest.raises(tracker.ProgressError, match="Markdown and PDF"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_current_phase_is_derived_from_first_open_stage(tmp_path: Path) -> None:
    document = _document(tmp_path)

    assert tracker.current_phase(_topic(document)) == "theory"


def test_record_stage_advances_revision_and_phase(tmp_path: Path) -> None:
    document = _document(tmp_path)
    evidence = [
        _artifact(tmp_path, "notes/topic.md"),
        _artifact(tmp_path, "notes/topic.pdf"),
    ]

    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="theory",
        new_status="complete",
        evidence=evidence,
        at=TODAY,
        actor="test",
    )

    assert document["revision"] == 2
    assert tracker.current_phase(_topic(document)) == "implementation"
    assert len(cast(list[object], document["history"])) == 2
    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


def test_complete_stage_without_evidence_is_rejected(tmp_path: Path) -> None:
    document = _document(tmp_path)

    with pytest.raises(tracker.ProgressError, match="needs evidence"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="theory",
            new_status="complete",
            evidence=[],
            at=TODAY,
            actor="test",
        )


def test_legacy_complete_understanding_review_remains_valid(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


def test_quiz_only_review_cannot_newly_complete_understanding(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[_review("Названо исключение из очевидной ветки")],
            at=TODAY,
            actor="test",
        )

    understanding = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, _topic(document)["stages"])[
            "understanding_check"
        ],
    )
    assert understanding == {"status": "pending", "evidence": []}
    assert document["revision"] == 1


def test_structured_review_can_newly_complete_understanding(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_through_verification(document, tmp_path)

    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="understanding_check",
        new_status="complete",
        evidence=_applied_understanding_evidence(),
        at=TODAY,
        actor="test",
    )

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


@pytest.mark.parametrize(
    ("proof_kind", "evidence_kind"),
    [
        ("test", "test"),
        ("trace", "artifact"),
        ("metric", "artifact"),
        ("experiment", "test"),
        ("diff", "commit"),
    ],
)
def test_applied_understanding_accepts_matching_proof_evidence_kind(
    tmp_path: Path,
    proof_kind: str,
    evidence_kind: str,
) -> None:
    document = _document(tmp_path)
    _complete_through_verification(document, tmp_path)
    if evidence_kind == "artifact":
        proof_evidence = _artifact(tmp_path, "notes/topic.md")
    elif evidence_kind == "commit":
        proof_evidence = _commit_evidence()
    else:
        proof_evidence = _test_evidence()
    proof_ref = cast(str, proof_evidence["ref"])

    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="understanding_check",
        new_status="complete",
        evidence=[
            _structured_understanding_review(
                proof_kind=proof_kind,
                proof_ref=proof_ref,
            ),
            proof_evidence,
        ],
        at=TODAY,
        actor="test",
    )

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


@pytest.mark.parametrize(
    "case",
    ["missing", "ref_mismatch", "failed_test", "wrong_kind", "extra"],
)
def test_understanding_completion_requires_exact_matching_proof_pair(
    tmp_path: Path,
    case: str,
) -> None:
    document = _document(tmp_path)
    review = _structured_understanding_review()
    proof: tracker.Evidence = _test_evidence()
    evidence: list[tracker.Evidence] = [review, proof]
    if case == "missing":
        evidence = [review]
    elif case == "ref_mismatch":
        proof["ref"] = "pytest -q other"
    elif case == "failed_test":
        proof["exit_code"] = 1
    elif case == "wrong_kind":
        understanding = cast(tracker.JsonObject, review["understanding"])
        understanding["proof"] = {"kind": "diff", "ref": proof["ref"]}
    else:
        evidence.append(_review("Лишняя запись review"))

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=evidence,
            at=TODAY,
            actor="test",
        )

    assert document["revision"] == 1


def test_static_validation_rejects_unmatched_active_understanding_proof(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_through_verification(document, tmp_path)
    stages = cast(tracker.JsonObject, _topic(document)["stages"])
    understanding = cast(tracker.JsonObject, stages["understanding_check"])
    understanding["status"] = "complete"
    understanding["evidence"] = [
        _structured_understanding_review(),
        _test_evidence(ref="pytest -q other"),
    ]

    with pytest.raises(
        tracker.ProgressError,
        match="must match an active proof evidence in the same stage",
    ):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


@pytest.mark.parametrize(
    "evidence",
    [[], [_review("Повторён ответ на вопрос по ветке кода")]],
)
def test_completed_understanding_only_accepts_new_structured_review(
    tmp_path: Path,
    evidence: list[tracker.Evidence],
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=evidence,
            at=TODAY,
            actor="test",
        )

    understanding = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, _topic(document)["stages"])[
            "understanding_check"
        ],
    )
    assert len(cast(list[object], understanding["evidence"])) == 1
    assert document["revision"] == 1


def test_partial_structured_review_cannot_complete_understanding(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[_structured_understanding_review(result="partial")],
            at=TODAY,
            actor="test",
        )


@pytest.mark.parametrize(
    ("field", "error"),
    [
        ("review_type", "review_type must be text"),
        ("agent_impact", "agent_impact must be text"),
        ("decision", "decision must be text"),
        ("invariant", "invariant must be text"),
        ("tradeoff", "tradeoff must be text"),
    ],
)
def test_structured_understanding_requires_each_text_field(
    tmp_path: Path,
    field: str,
    error: str,
) -> None:
    document = _document(tmp_path)
    evidence = _structured_understanding_review()
    understanding = cast(tracker.JsonObject, evidence["understanding"])
    understanding[field] = " "

    with pytest.raises(tracker.ProgressError, match=error):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[evidence],
            at=TODAY,
            actor="test",
        )


def test_structured_understanding_rejects_unknown_proof_kind(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    evidence = _structured_understanding_review()
    understanding = cast(tracker.JsonObject, evidence["understanding"])
    proof = cast(tracker.JsonObject, understanding["proof"])
    proof["kind"] = "quiz"

    with pytest.raises(tracker.ProgressError, match="proof.kind must be one of"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[evidence],
            at=TODAY,
            actor="test",
        )


@pytest.mark.parametrize("version", [None, True, 2, "1"])
def test_structured_understanding_requires_exact_integer_version(
    tmp_path: Path,
    version: object,
) -> None:
    document = _document(tmp_path)
    evidence = _structured_understanding_review()
    understanding = cast(tracker.JsonObject, evidence["understanding"])
    understanding["version"] = version

    with pytest.raises(tracker.ProgressError, match="version must be the integer 1"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[evidence],
            at=TODAY,
            actor="test",
        )


def test_structured_understanding_requires_proof_reference(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    evidence = _structured_understanding_review()
    understanding = cast(tracker.JsonObject, evidence["understanding"])
    proof = cast(tracker.JsonObject, understanding["proof"])
    proof["ref"] = " "

    with pytest.raises(tracker.ProgressError, match="proof.ref must be text"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[evidence],
            at=TODAY,
            actor="test",
        )


def test_accumulated_partial_does_not_allow_quiz_only_completion(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="understanding_check",
        new_status="in_progress",
        evidence=_applied_understanding_evidence(result="partial"),
        at=TODAY,
        actor="test",
    )

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[_review("Правильно угадана ветка")],
            at=TODAY,
            actor="test",
        )


def test_partial_structured_review_may_reference_proposed_proof(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_through_verification(document, tmp_path)
    stages = cast(tracker.JsonObject, _topic(document)["stages"])
    understanding = cast(tracker.JsonObject, stages["understanding_check"])
    understanding["status"] = "in_progress"
    understanding["evidence"] = [
        _structured_understanding_review(result="partial")
    ]

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


def test_reopened_legacy_understanding_requires_new_structured_review(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="understanding_check",
        new_status="in_progress",
        evidence=[_review("Требуется новая инженерная защита")],
        at=TODAY,
        actor="test",
        reopen=True,
    )

    with pytest.raises(
        tracker.ProgressError,
        match="exactly one newly supplied passed structured review",
    ):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=[_review("Повторён старый ответ")],
            at=TODAY,
            actor="test",
        )


def test_guided_code_tour_still_accepts_passed_review(tmp_path: Path) -> None:
    document = _document(tmp_path)
    _complete_through_verification(document, tmp_path)

    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="guided_code_tour",
        new_status="complete",
        evidence=[_review("Разобрана end-to-end трасса")],
        at=TODAY,
        actor="test",
    )

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


def test_complete_stage_requires_explicit_reopen_reason(tmp_path: Path) -> None:
    document = _document(tmp_path)
    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="theory",
        new_status="complete",
        evidence=[
            _artifact(tmp_path, "notes/topic.md"),
            _artifact(tmp_path, "notes/topic.pdf"),
        ],
        at=TODAY,
        actor="test",
    )

    with pytest.raises(tracker.ProgressError, match="use --reopen"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="theory",
            new_status="in_progress",
            evidence=[],
            at=TODAY,
            actor="test",
        )

    with pytest.raises(tracker.ProgressError, match="requires --review"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="theory",
            new_status="in_progress",
            evidence=[],
            at=TODAY,
            actor="test",
            reopen=True,
        )


def test_reopen_supersedes_evidence_and_invalidates_downstream(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)

    tracker.record_stage(
        document,
        module_id="01",
        topic_id="1.1",
        stage_name="theory",
        new_status="in_progress",
        evidence=[_review("Theory changed; repeat dependent work")],
        at=TODAY,
        actor="test",
        reopen=True,
    )

    stages = cast(tracker.JsonObject, _topic(document)["stages"])
    for stage_name in tracker.STAGES[1:]:
        stage = cast(tracker.JsonObject, stages[stage_name])
        assert stage["status"] == "pending"
        assert all(
            cast(tracker.JsonObject, item)["superseded_at_revision"] == 2
            for item in cast(list[object], stage["evidence"])
        )
    theory = cast(tracker.JsonObject, stages["theory"])
    theory_evidence = cast(list[object], theory["evidence"])
    assert cast(tracker.JsonObject, theory_evidence[0])[
        "superseded_at_revision"
    ] == 2
    assert cast(tracker.JsonObject, theory_evidence[-1])["role"] == "reopen_reason"
    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )

    with pytest.raises(tracker.ProgressError, match="needs evidence"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="theory",
            new_status="complete",
            evidence=[],
            at=TODAY,
            actor="test",
        )


def test_start_topic_requires_current_topic_complete(tmp_path: Path) -> None:
    document = _document(tmp_path, expected_topics=["1.1", "1.2"])

    with pytest.raises(tracker.ProgressError, match="current topic is not complete"):
        tracker.start_topic(
            document,
            module_id="01",
            topic_id="1.2",
            title="Next",
            at=TODAY,
            actor="test",
        )


def test_start_topic_creates_only_requested_topic(tmp_path: Path) -> None:
    document = _document(
        tmp_path,
        expected_topics=["1.1", "1.2", "1.3"],
    )
    _complete_topic(document, tmp_path)

    tracker.start_topic(
        document,
        module_id="01",
        topic_id="1.2",
        title="Next",
        at=TODAY,
        actor="test",
    )

    module = _module(document)
    topics = cast(tracker.JsonObject, module["topics"])
    assert set(topics) == {"1.1", "1.2"}
    assert "1.3" not in topics
    assert cast(tracker.JsonObject, document["current"]) == {
        "module": "01",
        "topic": "1.2",
    }


def test_completed_non_current_topic_accepts_new_structured_review(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path, expected_topics=["1.1", "1.2"])
    _complete_topic(document, tmp_path)
    tracker.start_topic(
        document,
        module_id="01",
        topic_id="1.2",
        title="Next",
        at=TODAY,
        actor="test",
    )

    _add_structured_understanding_review(document, topic_id="1.1")

    module = _module(document)
    topics = cast(tracker.JsonObject, module["topics"])
    first_topic = cast(tracker.JsonObject, topics["1.1"])
    stages = cast(tracker.JsonObject, first_topic["stages"])
    understanding = cast(tracker.JsonObject, stages["understanding_check"])
    evidence = cast(list[object], understanding["evidence"])
    assert len(evidence) == 3
    assert "understanding" not in cast(tracker.JsonObject, evidence[0])
    assert "understanding" in cast(tracker.JsonObject, evidence[1])
    assert cast(tracker.JsonObject, evidence[2])["kind"] == "test"
    assert document["revision"] == 3
    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )


def test_non_current_understanding_rejects_arbitrary_evidence_bundle(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path, expected_topics=["1.1", "1.2"])
    _complete_topic(document, tmp_path)
    tracker.start_topic(
        document,
        module_id="01",
        topic_id="1.2",
        title="Next",
        at=TODAY,
        actor="test",
    )
    evidence = _applied_understanding_evidence()
    evidence.append(_commit_evidence())

    with pytest.raises(tracker.ProgressError, match="only the current topic"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=evidence,
            at=TODAY,
            actor="test",
        )

    assert document["revision"] == 2


def test_start_topic_cannot_skip_expected_order(tmp_path: Path) -> None:
    document = _document(
        tmp_path,
        expected_topics=["1.1", "1.2", "1.3"],
    )
    _complete_topic(document, tmp_path)

    with pytest.raises(tracker.ProgressError, match="next topic must be 1.2"):
        tracker.start_topic(
            document,
            module_id="01",
            topic_id="1.3",
            title="Skipped",
            at=TODAY,
            actor="test",
        )


def test_expected_topics_must_match_module_and_be_contiguous(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path, expected_topics=["1.1", "1.3"])

    with pytest.raises(tracker.ProgressError, match=r"1\.1\.\.1\.N"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_stage_cannot_start_before_prerequisite(tmp_path: Path) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    stages = cast(tracker.JsonObject, topic["stages"])
    implementation = cast(tracker.JsonObject, stages["implementation"])
    implementation["status"] = "in_progress"

    with pytest.raises(tracker.ProgressError, match="before prerequisites"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_gate_cannot_pass_with_incomplete_topics_and_criteria(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    probe = _probe(
        tmp_path,
        tags={"module-01-complete": OID_A},
    )

    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_A,
        at=TODAY,
        actor="test",
    )
    with pytest.raises(tracker.ProgressError, match="incomplete topics"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=probe,
        )


def test_legacy_complete_topic_needs_structured_review_before_gate(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(tmp_path),
    )
    with pytest.raises(
        tracker.ProgressError,
        match="Gate needs an active passed applied structured understanding",
    ):
        tracker.pass_gate(
            document,
            module_id="01",
            adr=_artifact(tmp_path, "notes/topic.md"),
            evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
            git_tag="module-01-complete",
            git_revision=OID_A,
            at=TODAY,
            actor="test",
        )

    assert gate["status"] == "not_met"
    assert document["revision"] == 1


def test_legacy_passed_gate_remains_valid(tmp_path: Path) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    gate.update(
        {
            "status": "passed",
            "adr": _artifact(tmp_path, "notes/topic.md"),
            "evaluation_report": _artifact(tmp_path, "notes/topic.pdf"),
            "git_tag": "module-01-complete",
            "git_revision": OID_A,
        }
    )

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(
            tmp_path,
            tags={"module-01-complete": OID_A},
        ),
    )


def test_not_met_gate_rejects_understanding_policy_marker(tmp_path: Path) -> None:
    document = _document(tmp_path)
    gate = cast(tracker.JsonObject, _module(document)["gate"])
    gate["understanding_policy_version"] = 1

    with pytest.raises(
        tracker.ProgressError,
        match="must be absent while Gate is not_met",
    ):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


@pytest.mark.parametrize("policy_version", [True, 0, 2, "1", None])
def test_passed_gate_rejects_unknown_understanding_policy_version(
    tmp_path: Path,
    policy_version: object,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    gate = cast(tracker.JsonObject, _module(document)["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    gate.update(
        {
            "status": "passed",
            "adr": _artifact(tmp_path, "notes/topic.md"),
            "evaluation_report": _artifact(tmp_path, "notes/topic.pdf"),
            "git_tag": "module-01-complete",
            "git_revision": OID_A,
            "understanding_policy_version": policy_version,
        }
    )

    with pytest.raises(
        tracker.ProgressError,
        match="understanding_policy_version must be the integer 1",
    ):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(
                tmp_path,
                tags={"module-01-complete": OID_A},
            ),
        )


def test_policy_gate_requires_applied_understanding_for_every_topic(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    gate = cast(tracker.JsonObject, _module(document)["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    gate.update(
        {
            "status": "passed",
            "adr": _artifact(tmp_path, "notes/topic.md"),
            "evaluation_report": _artifact(tmp_path, "notes/topic.pdf"),
            "git_tag": "module-01-complete",
            "git_revision": OID_A,
            "understanding_policy_version": 1,
        }
    )

    with pytest.raises(
        tracker.ProgressError,
        match="needs an active passed applied structured understanding review",
    ):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(
                tmp_path,
                tags={"module-01-complete": OID_A},
            ),
        )


def test_gate_passes_only_with_exact_tag_and_committed_artifacts(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    _add_structured_understanding_review(document)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criteria = cast(tracker.JsonObject, gate["criteria"])
    criterion = cast(tracker.JsonObject, criteria["criterion"])
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    probe = _probe(
        tmp_path,
        tags={"module-01-complete": OID_A},
    )

    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_A,
        at=TODAY,
        actor="test",
    )

    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=probe,
    )
    assert tracker.module_status(module) == "complete"
    assert gate["status"] == "passed"
    assert gate["understanding_policy_version"] == 1


def test_stage_cannot_change_after_gate_passed(tmp_path: Path) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    _add_structured_understanding_review(document)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_A,
        at=TODAY,
        actor="test",
    )

    with pytest.raises(tracker.ProgressError, match="after Gate passed"):
        tracker.record_stage(
            document,
            module_id="01",
            topic_id="1.1",
            stage_name="understanding_check",
            new_status="complete",
            evidence=_applied_understanding_evidence(),
            at=TODAY,
            actor="test",
        )

    assert gate["status"] == "passed"


def test_gate_rejects_tag_pointing_to_other_commit(tmp_path: Path) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    _add_structured_understanding_review(document)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    probe = _probe(
        tmp_path,
        commits={OID_A: OID_A, OID_B: OID_B},
        tags={"module-01-complete": OID_B},
    )
    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_A,
        at=TODAY,
        actor="test",
    )

    with pytest.raises(tracker.ProgressError, match="points to another commit"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=probe,
        )


def test_gate_artifacts_must_belong_to_tagged_commit(tmp_path: Path) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    _add_structured_understanding_review(document)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    probe = _probe(
        tmp_path,
        commits={OID_A: OID_A, OID_B: OID_B},
        tags={"module-01-complete": OID_B},
    )
    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_B,
        at=TODAY,
        actor="test",
    )

    with pytest.raises(tracker.ProgressError, match="not stored in the tagged"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=probe,
        )


def test_start_module_requires_passed_gate_and_materializes_first_topic(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
    _add_structured_understanding_review(document)
    module = _module(document)
    gate = cast(tracker.JsonObject, module["gate"])
    criterion = cast(
        tracker.JsonObject,
        cast(tracker.JsonObject, gate["criteria"])["criterion"],
    )
    criterion["status"] = "complete"
    criterion["evidence"] = [_test_evidence()]
    tracker.pass_gate(
        document,
        module_id="01",
        adr=_artifact(tmp_path, "notes/topic.md"),
        evaluation_report=_artifact(tmp_path, "notes/topic.pdf"),
        git_tag="module-01-complete",
        git_revision=OID_A,
        at=TODAY,
        actor="test",
    )

    tracker.start_module(
        document,
        module_id="02",
        title="Next module",
        expected_topics=["2.1", "2.2"],
        first_topic_title="First",
        criteria={"next_gate": "Observable next criterion"},
        at=TODAY,
        actor="test",
    )

    modules = cast(tracker.JsonObject, document["modules"])
    next_module = cast(tracker.JsonObject, modules["02"])
    assert set(cast(tracker.JsonObject, next_module["topics"])) == {"2.1"}
    assert document["current"] == {"module": "02", "topic": "2.1"}
    tracker.validate_progress(
        document,
        repo_root=tmp_path,
        repository=_probe(
            tmp_path,
            tags={"module-01-complete": OID_A},
        ),
    )


def test_revision_conflict_does_not_modify_file(tmp_path: Path) -> None:
    document = _document(tmp_path)
    progress_path = tmp_path / "progress" / "progress.json"
    progress_path.parent.mkdir()
    progress_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    original = progress_path.read_bytes()

    with pytest.raises(tracker.ProgressError, match="revision conflict"):
        tracker.mutate_progress(
            progress_path=progress_path,
            repo_root=tmp_path,
            expected_revision=999,
            repository=_probe(tmp_path),
            mutation=lambda state: None,
        )

    assert progress_path.read_bytes() == original


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.json"
    progress_path.write_text('{"revision": 1, "revision": 2}', encoding="utf-8")

    with pytest.raises(tracker.ProgressError, match="duplicate JSON key"):
        tracker.load_progress(progress_path)


def test_artifact_path_cannot_escape_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(tracker.ProgressError, match="stay inside"):
        tracker.build_evidence(
            artifacts=["../outside.txt"],
            commits=[],
            test_command=None,
            test_result=None,
            test_exit_code=None,
            reviews=[],
            review_result="partial",
            verified_at=TODAY,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )


def test_build_evidence_rejects_uncommitted_artifact(tmp_path: Path) -> None:
    _document(tmp_path)
    probe = _probe(tmp_path)
    _write(tmp_path, "notes/new.md", b"new")

    with pytest.raises(tracker.ProgressError, match="not committed at HEAD"):
        tracker.build_evidence(
            artifacts=["notes/new.md"],
            commits=[],
            test_command=None,
            test_result=None,
            test_exit_code=None,
            reviews=[],
            review_result="partial",
            verified_at=TODAY,
            repo_root=tmp_path,
            repository=probe,
        )


def test_cli_builds_versioned_structured_understanding_review(
    tmp_path: Path,
) -> None:
    _document(tmp_path)
    arguments = tracker.parser().parse_args(
        [
            "record-stage",
            "--expected-revision",
            "1",
            "--module",
            "01",
            "--topic",
            "1.1",
            "--stage",
            "understanding_check",
            "--status",
            "complete",
            "--review",
            "Неоднозначное завершение tool",
            "--review-result",
            "passed",
            "--review-type",
            "failure_analysis",
            "--agent-impact",
            "Повтор может создать второй побочный эффект.",
            "--decision",
            "Не повторять до сверки idempotency key.",
            "--invariant",
            "Не более одного эффекта на ключ.",
            "--tradeoff",
            "Сверка увеличивает задержку восстановления.",
            "--proof-kind",
            "test",
            "--proof",
            "pytest -q tests/test_agent.py::test_no_repeat",
            "--test-command",
            "pytest -q tests/test_agent.py::test_no_repeat",
            "--test-result",
            "1 passed",
            "--test-exit-code",
            "0",
        ]
    )

    evidence = tracker._from_args(  # noqa: SLF001
        arguments,
        tmp_path,
        _probe(tmp_path),
    )

    assert len(evidence) == 2
    review = next(item for item in evidence if item["kind"] == "review")
    proof_evidence = next(item for item in evidence if item["kind"] == "test")
    understanding = cast(tracker.JsonObject, review["understanding"])
    assert understanding["version"] == 1
    assert understanding["review_type"] == "failure_analysis"
    assert understanding["agent_impact"] == (
        "Повтор может создать второй побочный эффект."
    )
    assert understanding["proof"] == {
        "kind": "test",
        "ref": "pytest -q tests/test_agent.py::test_no_repeat",
    }
    assert proof_evidence["ref"] == understanding["proof"]["ref"]
    assert proof_evidence["exit_code"] == 0


def test_cli_structured_understanding_fields_are_all_or_none(
    tmp_path: Path,
) -> None:
    _document(tmp_path)
    arguments = tracker.parser().parse_args(
        [
            "record-stage",
            "--expected-revision",
            "1",
            "--module",
            "01",
            "--topic",
            "1.1",
            "--stage",
            "understanding_check",
            "--status",
            "in_progress",
            "--review",
            "Частичный ответ",
            "--agent-impact",
            "Затронут повтор tool.",
        ]
    )

    with pytest.raises(tracker.ProgressError, match="must be provided together"):
        tracker._from_args(  # noqa: SLF001
            arguments,
            tmp_path,
            _probe(tmp_path),
        )


def test_cli_structured_understanding_requires_exactly_one_review(
    tmp_path: Path,
) -> None:
    _document(tmp_path)
    arguments = tracker.parser().parse_args(
        [
            "record-stage",
            "--expected-revision",
            "1",
            "--module",
            "01",
            "--topic",
            "1.1",
            "--stage",
            "understanding_check",
            "--status",
            "complete",
            "--review",
            "Первый review",
            "--review",
            "Второй review",
            "--review-result",
            "passed",
            "--review-type",
            "failure_analysis",
            "--agent-impact",
            "Затронут повтор tool.",
            "--decision",
            "Не повторять.",
            "--invariant",
            "Не более одного эффекта.",
            "--tradeoff",
            "Восстановление медленнее.",
            "--proof-kind",
            "test",
            "--proof",
            "test_no_repeat",
        ]
    )

    with pytest.raises(tracker.ProgressError, match="exactly one --review"):
        tracker._from_args(  # noqa: SLF001
            arguments,
            tmp_path,
            _probe(tmp_path),
        )


def test_cli_structured_understanding_is_rejected_for_other_stage(
    tmp_path: Path,
) -> None:
    _document(tmp_path)
    arguments = tracker.parser().parse_args(
        [
            "record-stage",
            "--expected-revision",
            "1",
            "--module",
            "01",
            "--topic",
            "1.1",
            "--stage",
            "guided_code_tour",
            "--status",
            "complete",
            "--review",
            "Разобрана трасса",
            "--review-result",
            "passed",
            "--review-type",
            "architecture_decision",
            "--agent-impact",
            "Затронуто состояние Agent.",
            "--decision",
            "Хранить состояние в runtime.",
            "--invariant",
            "Переходы состояния последовательны.",
            "--tradeoff",
            "Runtime сложнее.",
            "--proof-kind",
            "trace",
            "--proof",
            "successful-agent-trace",
        ]
    )

    with pytest.raises(tracker.ProgressError, match="only to understanding_check"):
        tracker._from_args(  # noqa: SLF001
            arguments,
            tmp_path,
            _probe(tmp_path),
        )


def test_mutation_is_atomic_and_appends_audit_event(tmp_path: Path) -> None:
    document = _document(tmp_path)
    progress_path = tmp_path / "progress" / "progress.json"
    progress_path.parent.mkdir()
    progress_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    evidence = [
        _artifact(tmp_path, "notes/topic.md"),
        _artifact(tmp_path, "notes/topic.pdf"),
    ]

    updated = tracker.mutate_progress(
        progress_path=progress_path,
        repo_root=tmp_path,
        expected_revision=1,
        repository=_probe(tmp_path),
        mutation=lambda state: tracker.record_stage(
            state,
            module_id="01",
            topic_id="1.1",
            stage_name="theory",
            new_status="complete",
            evidence=evidence,
            at=TODAY,
            actor="test",
        ),
    )

    reloaded = tracker.load_progress(progress_path)
    assert updated == reloaded
    assert reloaded["revision"] == 2
    history = cast(list[object], reloaded["history"])
    assert cast(tracker.JsonObject, history[-1])["action"] == "stage_recorded"


def test_validation_does_not_crash_on_malformed_topic(tmp_path: Path) -> None:
    document = _document(tmp_path)
    topic = _topic(document)
    topic["stages"] = {"missing": "not-an-object"}

    with pytest.raises(tracker.ProgressError, match="stages must be"):
        tracker.validate_progress(
            document,
            repo_root=tmp_path,
            repository=_probe(tmp_path),
        )
