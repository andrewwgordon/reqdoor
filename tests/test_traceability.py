"""Phase 2 — Traceability (FR-5): links, validation, suspect links, impact, orphans."""
import pytest

from app.extensions import db
from app.models import ArtefactKind
from app.services import (
    clear_suspect,
    create_relationship,
    create_version,
    graph_data,
    impact_report,
    orphan_artefacts,
    validate_relationship,
)
from tests.conftest import add_artefact, add_project, add_relationship_type, login_as


# ---------------------------------------------------------------------------
# Relationship creation + validation
# ---------------------------------------------------------------------------
def test_create_relationship_valid(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        rel = create_relationship(a, b, rt)

        assert rel.source_id == a.id
        assert rel.target_id == b.id
        assert rel.project_id == project
        assert rel.is_suspect is False
        assert len(a.outgoing_relationships) == 1


def test_duplicate_relationship_rejected(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        create_relationship(a, b, rt)
        with pytest.raises(ValueError):
            create_relationship(a, b, rt)


def test_cross_project_relationship_rejected(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        other = add_project(project_code="OTHER", name="Other").id
        a = add_artefact(project, title="A")
        b = add_artefact(other, title="B (other project)")
        with pytest.raises(ValueError):
            validate_relationship(a, b, rt)


def test_self_link_rejected(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        with pytest.raises(ValueError):
            validate_relationship(a, a, rt)


def test_kind_rules_enforced(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        rt.source_kinds = '["SYSTEM_REQ"]'
        db.session.commit()

        sys_req = add_artefact(project, title="SYS", kind=ArtefactKind.SYSTEM_REQ)
        story = add_artefact(project, title="STORY", kind=ArtefactKind.USER_STORY)
        target = add_artefact(project, title="T", kind=ArtefactKind.SOFTWARE_REQ)

        create_relationship(sys_req, target, rt)  # allowed source kind
        with pytest.raises(ValueError):
            create_relationship(story, target, rt)  # disallowed source kind


# ---------------------------------------------------------------------------
# Suspect links (FR-5)
# ---------------------------------------------------------------------------
def test_editing_source_marks_outgoing_link_suspect(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        create_version(a, "Initial draft")
        create_version(b, "Initial draft")
        rel = create_relationship(a, b, rt)

        assert rel.is_suspect is False
        a.title = "A changed"
        db.session.commit()
        create_version(a, "Edited")
        assert rel.is_suspect is True

        clear_suspect(rel)
        fresh = db.session.merge(rel)
        assert fresh.is_suspect is False


# ---------------------------------------------------------------------------
# Impact traversal + orphans
# ---------------------------------------------------------------------------
def test_forward_impact(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        c = add_artefact(project, title="C")
        d = add_artefact(project, title="D")
        # A -> B -> C  and  D -> B
        create_relationship(a, b, rt)
        create_relationship(b, c, rt)
        create_relationship(d, b, rt)

        report = impact_report(a.id)
        assert {row["artefact"].title: row["depth"] for row in report["impacted"]} == {
            "B": 1,
            "C": 2,
        }
        assert all(row["forward"] for row in report["impacted"])
        assert not any(row["reverse"] for row in report["impacted"])


def test_reverse_impact(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        c = add_artefact(project, title="C")
        d = add_artefact(project, title="D")
        create_relationship(a, b, rt)
        create_relationship(b, c, rt)
        create_relationship(d, b, rt)

        report = impact_report(c.id)  # C depends on B, which depends on A and D
        assert {row["artefact"].title for row in report["impacted"]} == {"A", "B", "D"}
        assert all(row["reverse"] for row in report["impacted"])


def test_orphan_detection(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        linked_a = add_artefact(project, title="L1", kind=ArtefactKind.SOFTWARE_REQ)
        linked_b = add_artefact(project, title="L2", kind=ArtefactKind.TEST_CASE)
        create_relationship(linked_a, linked_b, rt)
        add_artefact(project, title="ISOLATED")

        orphans = orphan_artefacts(project)
        assert [o.title for o in orphans] == ["ISOLATED"]


def test_graph_data_shape(app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A", kind=ArtefactKind.SYSTEM_REQ)
        b = add_artefact(project, title="B", kind=ArtefactKind.SOFTWARE_REQ)
        create_relationship(a, b, rt)

        nodes, edges = graph_data(a.id)
        assert {n["id"] for n in nodes} == {a.id, b.id}
        assert any(e["from"] == a.id and e["to"] == b.id for e in edges)
        assert all("suspect" in e and e["label"] for e in edges)


# ---------------------------------------------------------------------------
# UI smoke (traceability + impact pages)
# ---------------------------------------------------------------------------
def _login_client(client):
    resp = client.post(
        "/login/",
        data={"username": "admin", "password": "admin"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_trace_graph_and_impact_pages(client, app, project):
    with app.app_context():
        login_as(app)
        rt = add_relationship_type()
        a = add_artefact(project, title="A")
        b = add_artefact(project, title="B")
        create_relationship(a, b, rt)
        add_artefact(project, title="ISO")
        a_id = a.id

    _login_client(client)
    assert client.get("/tracegraphview/").status_code == 200
    resp = client.get(f"/tracegraphview/show/{a_id}/")
    assert resp.status_code == 200
    assert "network" in resp.get_data(as_text=True)

    resp = client.get(f"/impactview/analyse/{a_id}/")
    assert resp.status_code == 200
    assert "Impacted artefacts" in resp.get_data(as_text=True)

    assert client.get("/impactview/orphans/").status_code == 200