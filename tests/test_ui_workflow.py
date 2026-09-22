"""UI-workflow tests for the Phase 2 interface.

Covers the F.A.B. layout: the single project screen, the row Actions
(create-baseline-from-project, approve artefact), the outgoing trace-links tab
on the artefact detail page, the project workspace tabs, and the quickcharts
dashboard.
"""

from app.extensions import db
from app.models import (
    Artefact,
    ArtefactKind,
    Baseline,
    RequirementState,
)
from app.services import create_relationship, create_version
from tests.conftest import add_artefact, add_project, add_relationship_type, login_as


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# One project screen (the two-pane explorer duplicate was removed)
# ---------------------------------------------------------------------------
def test_project_list_renders_without_a_second_project_screen(client, app, project):
    """C7: the project explorer was a second door to the same data — deleted."""
    _login(client)
    with app.app_context():
        login_as(app)
        add_artefact(project, title="Pane A", status=RequirementState.APPROVED)

    assert client.get("/projectview/list/").status_code == 200
    assert client.get("/projectworkspaceview/list/").status_code == 404


# ---------------------------------------------------------------------------
# Row Actions
# ---------------------------------------------------------------------------
def test_create_baseline_action_from_project_page(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        approved = add_artefact(project, title="Approved", status=RequirementState.APPROVED)
        create_version(approved, "Initial draft")
        draft = add_artefact(project, title="Draft")
        create_version(draft, "Initial draft")

    resp = client.post(
        f"/projectview/action/create_baseline/{project}", follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        baseline = db.session.query(Baseline).filter_by(project_id=project).one()
        assert len(baseline.entries) == 1  # only the approved artefact
        assert "BL" in baseline.name

    # Second action on the same project (no-op guard path) still returns 200.
    resp = client.post(
        f"/projectview/action/create_baseline/{project}", follow_redirects=True
    )
    assert resp.status_code == 200


def test_approve_action_versions_the_artefact(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        a = add_artefact(project, title="Draft A")  # default DRAFT
        create_version(a, "Initial draft")  # v1
        a_id = a.id

    resp = client.post(f"/artefactview/action/approve/{a_id}", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        art = db.session.query(Artefact).get(a_id)
        assert art.status == RequirementState.APPROVED
        nums = sorted(v.version_number for v in art.versions)
        assert nums == [1, 2]
        assert art.versions[1].change_summary == "Status set to Approved"


# ---------------------------------------------------------------------------
# Related-view tabs
# ---------------------------------------------------------------------------
def test_project_show_is_a_project_workspace(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        add_artefact(project, title="Workspace artefact")

    resp = client.get(f"/projectview/show/{project}")
    assert resp.status_code == 200
    assert b"Workspace artefact" in resp.data


def test_artefact_show_includes_outgoing_links_tab(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        rt = add_relationship_type("VERIFIES", "Verifies")
        source = add_artefact(project, title="Source Art", status=RequirementState.APPROVED)
        create_version(source, "Initial draft")
        target = add_artefact(project, title="Target Art", status=RequirementState.APPROVED)
        create_version(target, "Initial draft")
        create_relationship(source, target, rt)
        source_id = source.id

    resp = client.get(f"/artefactview/show/{source_id}")
    assert resp.status_code == 200
    # The outgoing-link related view exposes the target artefact.
    assert b"Target Art" in resp.data


# ---------------------------------------------------------------------------
# Quickcharts dashboard
# ---------------------------------------------------------------------------
def test_dashboard_chart_pages_render(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        add_artefact(project, title="Chart fodder", status=RequirementState.APPROVED)

    for path in (
        "/artefactdistributionchartview/chart/",
        "/artefactperprojectchartview/chart/",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} failed"