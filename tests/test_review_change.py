"""Phase 2 — Reviews (FR-7) and Change Management (FR-8)."""
import pytest

from app.extensions import db
from app.models import (
    Approval,
    ChangeRequest,
    ChangeSetItem,
    ChangeState,
    Comment,
    Decision,
    Review,
    ReviewState,
)
from app.services import can_transition, create_version
from tests.conftest import add_artefact, login_as


def _login_client(client):
    resp = client.post(
        "/login/",
        data={"username": "admin", "password": "admin"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Workflow transition rules
# ---------------------------------------------------------------------------
def test_review_transition_rules():
    assert can_transition(ReviewState.PLANNED, ReviewState.ACTIVE)
    assert can_transition(ReviewState.ACTIVE, ReviewState.APPROVED)
    assert can_transition(ReviewState.COMMENT_RESOLUTION, ReviewState.APPROVED)
    assert can_transition(ReviewState.APPROVED, ReviewState.CLOSED)
    assert not can_transition(ReviewState.PLANNED, ReviewState.APPROVED)
    assert not can_transition(ReviewState.CLOSED, ReviewState.ACTIVE)


def test_change_transition_rules():
    assert can_transition(ChangeState.SUBMITTED, ChangeState.ANALYSED)
    assert can_transition(ChangeState.ANALYSED, ChangeState.APPROVED)
    assert can_transition(ChangeState.APPROVED, ChangeState.IMPLEMENTED)
    assert can_transition(ChangeState.IMPLEMENTED, ChangeState.VERIFIED)
    assert can_transition(ChangeState.VERIFIED, ChangeState.CLOSED)
    assert not can_transition(ChangeState.SUBMITTED, ChangeState.CLOSED)


# ---------------------------------------------------------------------------
# Review UI workflow
# ---------------------------------------------------------------------------
def test_review_create_activate_and_comment(client, app, project):
    with app.app_context():
        login_as(app)
        add_artefact(project, title="Under review")

    _login_client(client)
    resp = client.post(
        "/reviewview/add",
        data={
            "project": str(project),
            "title": "Design review",
            "description": "review the design",
            "status": "PLANNED",
            "due_date": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        review = db.session.query(Review).filter_by(title="Design review").one()
        rid = review.id
        assert review.status == ReviewState.PLANNED

    # Start the review through its action button (Milestone 3: one "Next step")
    resp = client.post(f"/reviewview/action/advance/{rid}", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.query(Review).get(rid).status == ReviewState.ACTIVE

    # Comment added against the review
    resp = client.post(
        "/commentview/add",
        data={"review": str(rid), "artefact": "", "parent": "", "body": "Looks good", "resolved": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        comment = db.session.query(Comment).filter_by(body="Looks good").one()
        assert comment.review_id == rid


def test_review_cannot_be_signed_before_it_is_active(client, app, project):
    """A review nobody has started yet has nothing to sign off (FR-P7)."""
    with app.app_context():
        login_as(app)
        review = Review(project_id=project, title="Not yet active")
        db.session.add(review)
        db.session.commit()
        rid = review.id

    _login_client(client)
    resp = client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.query(Review).get(rid).status == ReviewState.PLANNED
        assert db.session.query(Approval).filter_by(review_id=rid).count() == 0


def test_approval_captures_signing_user(client, app, project):
    """One click signs the review on behalf of the logged-in user (C17, FR-P7)."""
    with app.app_context():
        login_as(app)
        review = Review(
            project_id=project, title="Approval test", status=ReviewState.ACTIVE
        )
        db.session.add(review)
        db.session.commit()
        rid = review.id

    _login_client(client)
    resp = client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        approval = db.session.query(Approval).filter_by(review_id=rid).one()
        assert approval.decision == Decision.APPROVE
        assert approval.reviewer.username == "admin"

    # Re-signing replaces this user's position instead of stacking duplicates.
    client.post(f"/reviewview/action/sign_dissent/{rid}", follow_redirects=True)
    with app.app_context():
        approvals = db.session.query(Approval).filter_by(review_id=rid).all()
        assert len(approvals) == 1
        assert approvals[0].decision == Decision.REJECT


def test_approval_is_evidence_only(client, app, project):
    """Signatures can no longer be typed into a form - only earned by signing."""
    _login_client(client)
    assert client.get("/approvalview/add").status_code == 404
    with app.app_context():
        assert db.session.query(Approval).count() == 0


# ---------------------------------------------------------------------------
# Change management UI workflow
# ---------------------------------------------------------------------------
def test_change_request_workflow_end_to_end(client, app, project):
    with app.app_context():
        login_as(app)
        artefact_a = add_artefact(project, title="A")
        create_version(artefact_a, "Initial draft")
        artefact_id = artefact_a.id

    _login_client(client)
    resp = client.post(
        "/changerequestview/add",
        data={
            "project": str(project),
            "title": "CR-1",
            "description": "modify A",
            "priority": "HIGH",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        cr = db.session.query(ChangeRequest).filter_by(title="CR-1").one()
        cr_id = cr.id
        assert cr.requested_by.username == "admin"
        assert cr.status == ChangeState.SUBMITTED

    # Add a change-set item
    resp = client.post(
        "/changesetitemview/add",
        data={
            "change_request": str(cr_id),
            "artefact": str(artefact_id),
            "action": "UPDATE",
            "proposed_change": "change the content of A",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.query(ChangeSetItem).filter_by(change_request_id=cr_id).count() == 1

    # Walk the change workflow through the single "Next step" button (C16)
    for expected in (
        ChangeState.ANALYSED,
        ChangeState.APPROVED,
        ChangeState.IMPLEMENTED,
        ChangeState.VERIFIED,
        ChangeState.CLOSED,
    ):
        resp = client.post(
            f"/changerequestview/action/advance/{cr_id}", follow_redirects=True
        )
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.query(ChangeRequest).get(cr_id).status == expected