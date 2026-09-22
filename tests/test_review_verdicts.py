"""P0 tests — DNG-style review-from-baseline, per-artefact verdicts and progress.

Covers: verdicts pinning the frozen baseline version (never the live row),
single-verdict upsert, per-reviewer/overall progress, review scope, the
one-click review-from-baseline action, and the UI verdict/comment hooks.
"""

from app import appbuilder
from app.extensions import db
from app.models import (
    Artefact,
    ArtefactKind,
    Baseline,
    Comment,
    Decision,
    RequirementState,
    Review,
    ReviewAssignment,
    ReviewVerdict,
)
from app.services import (
    create_review_from_baseline,
    create_version,
    record_verdict,
    review_artefact_version,
    review_scope,
    review_progress,
    snapshot_baseline,
)
from tests.conftest import add_artefact, login_as

APPROVED = RequirementState.APPROVED


def _admin():
    return appbuilder.sm.find_user(username="admin")


def _add_baseline(project, artefacts, name="BL"):
    b = Baseline(project_id=project, name=name, description="test")
    db.session.add(b)
    db.session.commit()
    snapshot_baseline(b)
    return b


def _assign(review, user, role="Reviewer"):
    db.session.add(
        ReviewAssignment(
            review_id=review.id, reviewer=user, role_in_review=role
        )
    )
    db.session.commit()


def _add_approved(project, title):
    a = add_artefact(project, title=title, status=APPROVED)
    create_version(a, "Initial draft")
    return a


# ---------------------------------------------------------------------------
# Frozen-version resolution
# ---------------------------------------------------------------------------
def test_baseline_review_verdict_pins_frozen_version(app, project):
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "Frozen A")
        b = _add_baseline(project, [a])
        # Edit A after the baseline — live row moves to v2, baseline stays at v1.
        a.content = "edited after baseline"
        db.session.commit()
        create_version(a, "Edited")
        assert a.versions[-1].version_number == 2

        review = create_review_from_baseline(b)
        verdict = record_verdict(review, _admin(), a, Decision.APPROVE, "ok")

        assert verdict.artefact_version.version_number == 1, "must pin baseline v1"
        assert verdict.artefact_version.content == "hello"  # frozen, not the edit


def test_comment_resolves_to_frozen_baseline_version(app, project):
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "Frozen A")
        b = _add_baseline(project, [a])
        review = create_review_from_baseline(b)
        a.content = "v2 body"
        db.session.commit()
        create_version(a, "Edited")

        version = review_artefact_version(review, a)
        assert version is not None
        assert version.version_number == 1
        assert version.content == "hello"


def test_stream_review_resolves_to_latest_version(app, project):
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "Live A")
        review = Review(project_id=project, title="Stream review")
        db.session.add(review)
        db.session.commit()
        a.content = "v2 body"
        db.session.commit()
        create_version(a, "Edited")

        version = review_artefact_version(review, a)
        assert version.version_number == 2


# ---------------------------------------------------------------------------
# Verdicts (upsert) and progress
# ---------------------------------------------------------------------------
def test_verdict_is_an_upsert(app, project):
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "Upsert A")
        b = _add_baseline(project, [a])
        review = create_review_from_baseline(b)

        record_verdict(review, _admin(), a, Decision.REJECT, "needs work")
        record_verdict(review, _admin(), a, Decision.APPROVE, "now approved")

        rows = db.session.query(ReviewVerdict).filter_by(review_id=review.id).all()
        assert len(rows) == 1
        assert rows[0].decision == Decision.APPROVE
        assert rows[0].comment == "now approved"


def test_review_progress_all_decided(app, project):
    with app.app_context():
        login_as(app)
        a1 = _add_approved(project, "A1")
        a2 = _add_approved(project, "A2")
        b = _add_baseline(project, [a1, a2])
        review = create_review_from_baseline(b)
        _assign(review, _admin())

        record_verdict(review, _admin(), a1, Decision.APPROVE)
        record_verdict(review, _admin(), a2, Decision.APPROVE)

        progress = review_progress(review)
        assert progress["total"] == 2
        assert progress["reviewers"]["admin"]["percent"] == 100
        assert progress["overall"] == 100


def test_review_progress_counts_only_scope(app, project):
    with app.app_context():
        login_as(app)
        a1 = _add_approved(project, "A1")
        a2 = _add_approved(project, "A2")
        b = _add_baseline(project, [a1, a2])
        review = create_review_from_baseline(b)
        _assign(review, _admin())

        record_verdict(review, _admin(), a1, Decision.APPROVE)  # 1 of 2

        progress = review_progress(review)
        info = progress["reviewers"]["admin"]
        assert info["decided"] == 1
        assert info["required"] == 2
        assert info["percent"] == 50
        assert progress["overall"] == 50


def test_review_scope_matches_baseline_not_project(app, project):
    with app.app_context():
        login_as(app)
        in_scope = _add_approved(project, "In scope")
        baseline = _add_baseline(project, [in_scope])
        # unapproved artefact: not baselined, must not be in scope
        add_artefact(project, title="Not approved")  # DRAFT, no version

        review = create_review_from_baseline(baseline)
        scope = review_scope(review)
        assert [a.id for a in scope] == [in_scope.id]


def test_stream_review_scope_is_project_artefacts(app, project):
    with app.app_context():
        login_as(app)
        a1 = _add_approved(project, "A1")
        a2 = add_artefact(project, title="A2 draft")
        review = Review(project_id=project, title="Stream review")
        db.session.add(review)
        db.session.commit()
        assert {a.id for a in review_scope(review)} == {a1.id, a2.id}


# ---------------------------------------------------------------------------
# UI workflows
# ---------------------------------------------------------------------------
def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def test_ui_create_review_from_baseline_action(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "A")
        baseline = _add_baseline(project, [a])
        baseline_id = baseline.id

    resp = client.post(
        f"/baselineview/action/create_review/{baseline_id}", follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        review = db.session.query(Review).filter_by(basis_baseline_id=baseline_id).one()
        assert review.title == "BL Review"
        assert len(review_scope(review)) == 1


def test_ui_verdict_add_pins_frozen_version(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "A")
        baseline = _add_baseline(project, [a])
        review = create_review_from_baseline(baseline)
        review_id, artefact_id = review.id, a.id

    resp = client.post(
        "/reviewverdictview/add",
        data={
            "review": str(review_id),
            "artefact": str(artefact_id),
            "decision": "APPROVE",
            "comment": "matches CQ- list",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        verdict = (
            db.session.query(ReviewVerdict)
            .filter_by(review_id=review_id, artefact_id=artefact_id)
            .one()
        )
        assert verdict.reviewer.username == "admin"  # signed by current user
        assert verdict.artefact_version.version_number == 1


def test_ui_verdict_rejects_out_of_scope_artefact(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "A")
        baseline = _add_baseline(project, [a])
        review = create_review_from_baseline(baseline)
        outside = _add_approved(project, "Outside")  # NOT in the baseline
        review_id, outside_id = review.id, outside.id

    resp = client.post(
        "/reviewverdictview/add",
        data={
            "review": str(review_id),
            "artefact": str(outside_id),
            "decision": "APPROVE",
            "comment": "",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        count = (
            db.session.query(ReviewVerdict)
            .filter_by(review_id=review_id, artefact_id=outside_id)
            .count()
        )
        assert count == 0


def test_ui_comment_pins_reviewed_version(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        a = _add_approved(project, "A")
        baseline = _add_baseline(project, [a])
        review = create_review_from_baseline(baseline)
        a.content = "v2 body"
        db.session.commit()
        create_version(a, "Edited")
        review_id, artefact_id = review.id, a.id

    resp = client.post(
        "/commentview/add",
        data={
            "review": str(review_id),
            "artefact": str(artefact_id),
            "parent": "",
            "body": "Please look at the fail-safe clause.",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        comment = (
            db.session.query(Comment)
            .filter_by(review_id=review_id, artefact_id=artefact_id)
            .order_by(Comment.id.desc())
            .first()
        )
        assert comment is not None
        assert comment.artefact_version_id is not None
        assert comment.artefact_version.version_number == 1  # frozen, not live v2