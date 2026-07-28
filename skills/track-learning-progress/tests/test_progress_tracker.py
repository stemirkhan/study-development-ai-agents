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


def _test_evidence(exit_code: int = 0) -> tracker.Evidence:
    return {
        "kind": "test",
        "ref": "pytest -q",
        "result": "1 passed" if exit_code == 0 else "1 failed",
        "exit_code": exit_code,
        "git_revision": OID_A,
        "verified_at": TODAY,
    }


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


def test_gate_passes_only_with_exact_tag_and_committed_artifacts(
    tmp_path: Path,
) -> None:
    document = _document(tmp_path)
    _complete_topic(document, tmp_path)
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


def test_gate_rejects_tag_pointing_to_other_commit(tmp_path: Path) -> None:
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
