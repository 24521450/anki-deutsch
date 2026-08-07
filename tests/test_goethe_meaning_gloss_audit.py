from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import goethe_meaning_gloss_audit as audit  # noqa: E402


def test_snapshot_build_covers_the_canonical_catalog_without_drift():
    manifest = audit.load_manifest()
    report = audit.build_report(
        audit.load_snapshot_rows(),
        manifest,
        "checked-in snapshot",
    )

    audit.validate_report(report, manifest)
    assert len(report) == 3425
    assert sum(row["card_count"] for row in report) == 6850
    assert all(row["meaning_matches_manifest"] for row in report)


def test_spannend_is_the_confirmed_synonym_regression_case():
    decision = audit.DECISIONS["A2-0935"]

    assert decision.decision == "PROPOSE_REVISE"
    assert decision.recommended_meaning_en == "exciting"
    assert "gripping" in decision.note_en
    assert "suspenseful" in decision.note_en


def test_dictionary_backed_multi_gloss_exceptions_are_not_auto_collapsed():
    manifest = audit.load_manifest()
    report = audit.build_report(audit.load_snapshot_rows(), manifest, "snapshot")
    by_id = {row["source_id"]: row for row in report}

    assert by_id["A2-0078"]["decision"] == "KEEP_EXPLAINED"
    assert by_id["B1-MAIN-2872"]["decision"] == "KEEP_EXPLAINED"


def test_all_65_direct_review_candidates_are_resolved_with_secondary_sources():
    manifest = audit.load_manifest()
    report = audit.build_report(audit.load_snapshot_rows(), manifest, "snapshot")
    by_id = {row["source_id"]: row for row in report}
    candidates = [by_id[source_id] for source_id in audit.DIRECT_REVIEW_CANDIDATE_IDS]

    assert len(candidates) == 65
    assert all(row["decision"] != "REVIEW" for row in candidates)
    assert all(len(row["secondary_evidence"]) >= 2 for row in candidates)
    assert by_id["A1-84886454532"]["recommended_meaning_en"] == "to get; to receive"
    assert by_id["A2-0316"]["recommended_meaning_en"] == "celebration; party"
    assert by_id["A2-0758"]["recommended_meaning_en"] == "sweater"
