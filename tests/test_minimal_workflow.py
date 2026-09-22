"""PoC acceptance tests — the minimum requirements management process.

Exercises the FR-P5 loop from `docs/minimal_spec.md`:
Project → Artefact → immutable version on every change → Baseline (snapshot
of approved artefact versions) → verify / restore.
"""
import pytest

from app.extensions import db
from app.models import Artefact, Baseline, BaselineEntry, RequirementState
from app.services import (
    create_version,
    latest_version,
    reconcile_artefact,
    restore_version,
    snapshot_baseline,
)
from tests.conftest import add_artefact, login_as


APPROVED = RequirementState.APPROVED


# ---------------------------------------------------------------------------
# Versioning (FR-P3)
# ---------------------------------------------------------------------------
def test_initial_create_produces_version_1(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project)
        create_version(a, "Initial draft")

        assert [v.version_number for v in a.versions] == [1]
        v1 = a.versions[0]
        assert v1.title == "Requirement 1"
        assert v1.content == "hello"
        assert v1.change_summary == "Initial draft"
        assert v1.created_by.username == "admin"


def test_each_edit_creates_incrementing_version(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project)
        create_version(a, "Initial draft")

        a.title = "Requirement 1 revised"
        db.session.commit()
        create_version(a, "Edited")

        versions = sorted(a.versions, key=lambda v: v.version_number)
        assert [v.version_number for v in versions] == [1, 2]
        assert versions[0].title == "Requirement 1"
        assert versions[1].title == "Requirement 1 revised"


def test_latest_version_returns_most_recent(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project)
        create_version(a, "Initial draft")
        a.content = "second"
        db.session.commit()
        create_version(a, "Edited")

        latest = latest_version(a)
        assert latest.version_number == 2
        assert latest.content == "second"


def test_restore_records_new_version(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, content="original content")
        create_version(a, "Initial draft")
        a.content = "changed content"
        db.session.commit()
        create_version(a, "Edited")

        v1 = next(v for v in a.versions if v.version_number == 1)
        restore_version(a, v1)

        refreshed = db.session.query(Artefact).get(a.id)
        assert refreshed.content == "original content"
        assert len(refreshed.versions) == 3
        assert max(v.version_number for v in refreshed.versions) == 3


# ---------------------------------------------------------------------------
# Baselines (FR-P4)
# ---------------------------------------------------------------------------
def test_baseline_snapshots_latest_approved_version_of_each_artefact(app, project):
    with app.app_context():
        login_as(app)
        a1 = add_artefact(project, title="Artefact A", status=APPROVED)
        b1 = add_artefact(project, title="Artefact B", status=APPROVED)
        create_version(a1, "Initial draft")  # A v1
        create_version(b1, "Initial draft")  # B v1
        a1.title = "Artefact A revised"
        db.session.commit()
        create_version(a1, "Edited")  # A v2

        baseline = Baseline(project_id=project, name="BL-1", description="release 1")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)

        assert len(baseline.entries) == 2
        by_artefact = {e.artefact_id: e for e in baseline.entries}
        assert by_artefact[a1.id].artefact_version.version_number == 2
        assert by_artefact[a1.id].artefact_version.title == "Artefact A revised"
        assert by_artefact[b1.id].artefact_version.version_number == 1


def test_baseline_skips_unapproved_artefacts(app, project):
    """Only currently-approved versions are baselined (FR-P4)."""
    with app.app_context():
        login_as(app)
        approved = add_artefact(project, title="Approved A", status=APPROVED)
        create_version(approved, "Initial draft")
        draft = add_artefact(project, title="Draft B")  # default DRAFT
        create_version(draft, "Initial draft")

        baseline = Baseline(project_id=project, name="BL-APPROVED-ONLY")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)

        assert [e.artefact_id for e in baseline.entries] == [approved.id]


def test_baseline_excludes_version_moved_out_of_approved(app, project):
    """If the latest version is no longer Approved, the artefact is not baselined."""
    with app.app_context():
        login_as(app)
        a = add_artefact(project, title="A", status=APPROVED)
        create_version(a, "Initial draft")  # v1 APPROVED
        a.status = RequirementState.PROPOSED  # re-opened after review
        db.session.commit()
        create_version(a, "Edited")  # v2 PROPOSED

        baseline = Baseline(project_id=project, name="BL-NO-REGRESSION")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)

        assert len(baseline.entries) == 0


def test_baseline_frozen_despite_later_edits(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, title="A", content="v1", status=APPROVED)
        create_version(a, "Initial draft")  # A v1
        a.content = "v2"
        db.session.commit()
        create_version(a, "Edited")  # A v2

        baseline = Baseline(project_id=project, name="BL-FROZEN")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)
        frozen_version = baseline.entries[0].artefact_version.version_number
        assert frozen_version == 2

        # Further edits do not alter the baseline snapshot
        a.content = "v3"
        db.session.commit()
        create_version(a, "Edited")  # A v3
        assert latest_version(a).version_number == 3

        entry = db.session.query(BaselineEntry).filter(
            BaselineEntry.baseline_id == baseline.id,
            BaselineEntry.artefact_id == a.id,
        ).one()
        assert entry.artefact_version.version_number == 2
        assert entry.artefact_version.content == "v2"


def test_baseline_implicit_version_for_unversioned_approved_artefact(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, status=APPROVED)  # never versioned explicitly
        baseline = Baseline(project_id=project, name="BL-IMPLICIT")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)

        assert len(baseline.entries) == 1
        entry = baseline.entries[0]
        assert entry.artefact_version.version_number == 1
        assert entry.artefact_version.change_summary == "Initial snapshot for baseline"


def test_baseline_snapshot_is_idempotent(app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, status=APPROVED)
        create_version(a, "Initial draft")
        baseline = Baseline(project_id=project, name="BL-IDEMPOTENT")
        db.session.add(baseline)
        db.session.commit()

        snapshot_baseline(baseline)
        snapshot_baseline(baseline)  # second call must not duplicate entries
        assert len(baseline.entries) == 1
        assert (
            db.session.query(BaselineEntry)
            .filter(BaselineEntry.baseline_id == baseline.id)
            .count()
            == 1
        )


# ---------------------------------------------------------------------------
# Display invariant (FR-P3 — artefact shows latest version's properties)
# ---------------------------------------------------------------------------
def test_artefact_reconciles_to_latest_version(app, project):
    """A row that drifted from its latest version is corrected on render."""
    with app.app_context():
        login_as(app)
        a = add_artefact(project, content="v1 body", status=APPROVED)
        create_version(a, "Initial draft")  # v1
        a.content = "v2 body"
        db.session.commit()
        create_version(a, "Edited")  # v2

        # Rogue write that bypassed versioning — the row now disagrees with v2.
        a.content = "rogue body"
        db.session.commit()

        assert reconcile_artefact(a) is True
        refreshed = db.session.query(Artefact).get(a.id)
        assert refreshed.content == "v2 body"  # latest version is authoritative

        # No drift => reconcile is a no-op.
        assert reconcile_artefact(refreshed) is False


def test_ui_artefact_show_displays_latest_version(client, app, project):
    """The artefact detail page shows the latest version's properties."""
    _login_client(client)
    with app.app_context():
        login_as(app)
        a = add_artefact(project, title="Reconciled", content="v1 body", status=APPROVED)
        create_version(a, "Initial draft")  # v1
        a.content = "v2 body"
        db.session.commit()
        create_version(a, "Edited")  # v2
        a_id = a.id
        # Drift the row, then GET the detail page (which must reconcile).
        a.content = "rogue body"
        db.session.commit()

    resp = client.get(f"/artefactview/show/{a_id}")
    assert resp.status_code == 200
    assert "v2 body" in resp.get_data(as_text=True)

    with app.app_context():
        a = db.session.query(Artefact).get(a_id)
        assert a.content == "v2 body"


# ---------------------------------------------------------------------------
# UI-level smoke of the minimum process (see docs/minimal_spec.md §6)
# ---------------------------------------------------------------------------
def _login_client(client):
    resp = client.post(
        "/login/",
        data={"username": "admin", "password": "admin"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_ui_add_artefact_records_version_1(client, app, project):
    _login_client(client)
    resp = client.post(
        "/artefactview/add",
        data={
            "project": str(project),
            "kind": "SYSTEM_REQ",
            "title": "UI Requirement",
            "content": "typed in browser",
            "status": "DRAFT",
            "priority": "MEDIUM",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        a = db.session.query(Artefact).filter_by(title="UI Requirement").one()
        assert a.versions[0].version_number == 1
        assert a.versions[0].change_summary == "Initial draft"


def test_ui_baseline_snapshots_approved_project_versions(client, app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, title="A", status=APPROVED)
        create_version(a, "Initial draft")
        d = add_artefact(project, title="Draft D", status=RequirementState.DRAFT)
        create_version(d, "Initial draft")

    _login_client(client)
    resp = client.post(
        "/baselineview/add",
        data={"project": str(project), "name": "UI Baseline", "description": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        bl = db.session.query(Baseline).filter_by(name="UI Baseline").one()
        assert len(bl.entries) == 1  # only the approved artefact is baselined
        # Contents tab is reachable
    resp = client.get(f"/baselineview/show/{bl.id}")
    assert resp.status_code == 200


def test_ui_artefact_in_baseline_cannot_be_deleted(client, app, project):
    with app.app_context():
        login_as(app)
        a = add_artefact(project, status=APPROVED)
        create_version(a, "Initial draft")
        baseline = Baseline(project_id=project, name="BL-GUARD")
        db.session.add(baseline)
        db.session.commit()
        snapshot_baseline(baseline)
        a_id = a.id  # read inside the session it belongs to

    _login_client(client)
    client.post(f"/artefactview/delete/{a_id}", follow_redirects=True)
    with app.app_context():
        alive = db.session.query(Artefact).get(a_id)
        assert alive is not None, "Artefact referenced by a baseline must not be deleted"