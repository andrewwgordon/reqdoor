"""Milestone 0 regression tests — UI/workflow defects (changes C1–C6).

Each test names the defect it pins down:

C1  ``Review`` "Approve review" / "Close review" actions raised ``TypeError`` (500).
C2  Row actions raised ``AttributeError`` (500) when F.A.B. dispatched them in
    bulk from the list view (they are written for a single record).
C3  The traceability and impact pickers re-rendered themselves and did nothing.
C4  Actions dumped the user on the (empty) index page instead of the record.
C5  Context-less comments and out-of-scope verdicts were silently accepted.
C6  Debug print, legacy ``Query.get()`` calls and a version per no-op save.
"""

import io
import re
from pathlib import Path

import pytest

import app as app_package
from app.extensions import db
from app.models import (
    Approval,
    Artefact,
    ArtefactKind,
    ArtefactVersion,
    Baseline,
    ChangeRequest,
    ChangeState,
    Comment,
    Decision,
    RequirementState,
    Review,
    ReviewState,
    ReviewVerdict,
)
from app.services import (
    create_review_from_baseline,
    create_version,
    record_verdict,
    snapshot_baseline,
    validate_verdict_scope,
)
from tests.conftest import add_artefact, add_project, login_as


LEGACY_QUERY_GET = re.compile(r"\.query\([^)]*\)\s*\.get\(")


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def _add_review(project_id, status=ReviewState.ACTIVE):
    r = Review(project_id=project_id, title=f"Review {status.value}", status=status)
    db.session.add(r)
    db.session.commit()
    return r


def _add_change_request(project_id, user_id, status=ChangeState.SUBMITTED):
    cr = ChangeRequest(
        project_id=project_id,
        title=f"CR {status.value}",
        status=status,
        requested_by_id=user_id,
    )
    db.session.add(cr)
    db.session.commit()
    return cr


def _admin_id(app):
    return app.appbuilder.sm.find_user(username="admin").id


# ---------------------------------------------------------------------------
# C1 (surfaces re-shaped by C16/C17) — workflow actions never crash and never
# move a record somewhere illegal
# ---------------------------------------------------------------------------
# What each action does from each state: illegal combinations must leave the
# record exactly where it was (and never 500).
REVIEW_ADVANCE_MATRIX = {
    ReviewState.PLANNED: ReviewState.ACTIVE,
    ReviewState.ACTIVE: ReviewState.APPROVED,
    ReviewState.COMMENT_RESOLUTION: ReviewState.APPROVED,
    ReviewState.APPROVED: ReviewState.CLOSED,
    ReviewState.CLOSED: ReviewState.CLOSED,  # nothing left to do
}
REQUEST_CHANGES_MATRIX = {
    ReviewState.PLANNED: ReviewState.PLANNED,
    ReviewState.ACTIVE: ReviewState.COMMENT_RESOLUTION,
    ReviewState.COMMENT_RESOLUTION: ReviewState.COMMENT_RESOLUTION,
    ReviewState.APPROVED: ReviewState.APPROVED,
    ReviewState.CLOSED: ReviewState.CLOSED,
}
SIGN_MATRIX = {state: state for state in ReviewState}


@pytest.mark.parametrize(
    "action,matrix",
    [
        ("advance", REVIEW_ADVANCE_MATRIX),
        ("request_changes", REQUEST_CHANGES_MATRIX),
        ("sign_off", SIGN_MATRIX),
        ("sign_dissent", SIGN_MATRIX),
    ],
)
def test_review_actions_are_safe_from_every_state(client, app, project, action, matrix):
    """C1 was a TypeError (500) on two of these; C16 replaced all three buttons."""
    _login(client)
    for start_state, expected in matrix.items():
        with app.app_context():
            login_as(app)
            rid = _add_review(project, start_state).id
        resp = client.post(f"/reviewview/action/{action}/{rid}", follow_redirects=True)
        assert resp.status_code == 200, f"{action} from {start_state.value}"
        with app.app_context():
            assert db.session.get(Review, rid).status == expected, (
                f"{action} from {start_state.value}"
            )


def test_review_sign_off_records_a_signature_only_when_decidable(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        planned = _add_review(project, ReviewState.PLANNED).id
        active = _add_review(project, ReviewState.ACTIVE).id

    client.post(f"/reviewview/action/sign_off/{planned}", follow_redirects=True)
    with app.app_context():
        assert db.session.query(Approval).filter_by(review_id=planned).count() == 0

    client.post(f"/reviewview/action/sign_off/{active}", follow_redirects=True)
    with app.app_context():
        approval = db.session.query(Approval).filter_by(review_id=active).one()
        assert approval.decision == Decision.APPROVE
        assert approval.reviewer.username == "admin"


def test_comment_resolution_is_reachable_from_a_button(client, app, project):
    """C16: this state used to need a hidden status edit (FR-P7)."""
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _add_review(project, ReviewState.ACTIVE).id

    client.post(f"/reviewview/action/request_changes/{rid}", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.COMMENT_RESOLUTION
    # ...and from there the single advance button approves it.
    client.post(f"/reviewview/action/advance/{rid}", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.APPROVED


def test_review_can_be_driven_end_to_end_through_the_ui(client, app, project):
    """The whole review lifecycle is clickable, one legal button at a time."""
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _add_review(project, ReviewState.PLANNED).id

    for expected in (
        ReviewState.ACTIVE,
        ReviewState.APPROVED,
        ReviewState.CLOSED,
    ):
        resp = client.post(f"/reviewview/action/advance/{rid}", follow_redirects=False)
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Review, rid).status == expected, expected

    # A closed review stays put when the button is pressed again.
    client.post(f"/reviewview/action/advance/{rid}", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.CLOSED


def test_change_request_lifecycle_actions_still_work(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        cr = _add_change_request(project, _admin_id(app))
        crid = cr.id

    for expected in (
        ChangeState.ANALYSED,
        ChangeState.APPROVED,
        ChangeState.IMPLEMENTED,
        ChangeState.VERIFIED,
        ChangeState.CLOSED,
    ):
        resp = client.post(
            f"/changerequestview/action/advance/{crid}", follow_redirects=False
        )
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(ChangeRequest, crid).status == expected, expected


# ---------------------------------------------------------------------------
# C2 — actions are single-record: bulk dispatch must not blow up
# ---------------------------------------------------------------------------
ACTIONS = [
    ("artefactview", "approve"),
    ("artefactview", "obsolete"),
    ("artefactview", "trace"),
    ("projectview", "create_baseline"),
    ("reviewview", "advance"),
    ("reviewview", "request_changes"),
    ("reviewview", "sign_off"),
    ("reviewview", "sign_dissent"),
    ("changeRequestview", "advance"),
    ("baselineview", "create_review"),
    ("relationshipview", "markvalid"),
    ("artefactversionview", "restore"),
]


@pytest.mark.parametrize("view,action", ACTIONS)
def test_bulk_action_dispatch_is_refused_gracefully(client, app, project, view, action):
    """A hand-made bulk POST never reaches a 500 (C2).

    ``SingleRecordActionMixin`` answers every bulk dispatch with a flash; before
    Milestone 3 the read-only views refused it with a bare 403 instead.
    """
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Bulk target")
        create_version(artefact, "Initial draft")
        review = _add_review(project, ReviewState.PLANNED)
        cr = _add_change_request(project, _admin_id(app))
        aid, rid, crid = artefact.id, review.id, cr.id
        pks = [str(aid), str(rid), str(crid), str(project), "1"]

    resp = client.post(
        f"/{view.lower()}/action_post",
        data={"action": action, "rowid": pks},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"one record at a time" in resp.data
    with app.app_context():
        # nothing moved: the bulk request changed no state
        assert db.session.get(Artefact, aid).status == RequirementState.DRAFT
        assert db.session.get(Review, rid).status == ReviewState.PLANNED
        assert db.session.get(ChangeRequest, crid).status == ChangeState.SUBMITTED
        assert db.session.query(Baseline).count() == 0
    with app.app_context():
        # nothing moved: the bulk request changed no state
        assert db.session.get(Artefact, aid).status == RequirementState.DRAFT
        assert db.session.get(Review, rid).status == ReviewState.PLANNED
        assert db.session.get(ChangeRequest, crid).status == ChangeState.SUBMITTED
        assert db.session.query(Baseline).count() == 0


def test_list_pages_offer_no_bulk_action_controls(client, app, project):
    """With single-record actions F.A.B. drops the row checkboxes + Actions menu."""
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="List row")
        create_version(artefact, "Initial draft")
        _add_review(project)

    for url, action_css in (
        ("/artefactview/list/", "approve_menu_item"),
        ("/reviewview/list/", "activate_menu_item"),
        ("/changerequestview/list/", "analyse_menu_item"),
    ):
        body = client.get(url).data.decode("utf-8", "replace")
        assert "check_all" not in body, url
        assert action_css not in body, url
        assert "action_post" not in body, url


def test_record_actions_are_still_on_the_show_pages(client, app, project):
    """Hiding them from the list must not hide them from the record page."""
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Show row")
        create_version(artefact, "Initial draft")
        aid = artefact.id
        rid = _add_review(project).id

    body = client.get(f"/artefactview/show/{aid}").data.decode("utf-8", "replace")
    for button in ("approve", "obsolete", "trace"):
        assert button in body, button

    body = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    for button in ("advance", "request_changes", "sign_off", "sign_dissent"):
        assert button in body, button


# ---------------------------------------------------------------------------
# C3 — the analysis pickers do something
# ---------------------------------------------------------------------------
def test_trace_graph_picker_submits_to_the_graph(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        aid = add_artefact(project, title="Graph me").id

    resp = client.get(f"/tracegraphview/?pk={aid}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/tracegraphview/show/{aid}/")
    page = client.get(f"/tracegraphview/?pk={aid}", follow_redirects=True)
    assert b'id="network"' in page.data


def test_impact_picker_submits_to_the_report(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        aid = add_artefact(project, title="Analyse me").id

    resp = client.get(f"/impactview/?pk={aid}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/impactview/analyse/{aid}/")
    page = client.get(f"/impactview/?pk={aid}", follow_redirects=True)
    assert b"Impacted artefacts" in page.data


def test_analysis_pages_redirect_home_for_unknown_artefacts(client, app):
    _login(client)
    for url, back in (
        ("/tracegraphview/show/999999/", "/tracegraphview/"),
        ("/impactview/analyse/999999/", "/impactview/"),
    ):
        resp = client.get(url, follow_redirects=False)
        assert resp.status_code == 302, url
        assert resp.headers["Location"].startswith(back), url
        page = client.get(url, follow_redirects=True)
        assert b"Artefact not found" in page.data


# ---------------------------------------------------------------------------
# C4 — actions return the user to the record they acted on
# ---------------------------------------------------------------------------
def test_artefact_approve_returns_to_the_artefact(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        aid = add_artefact(project, title="Approve me").id

    resp = client.post(f"/artefactview/action/approve/{aid}", follow_redirects=False)

    assert resp.headers["Location"].endswith(f"/artefactview/show/{aid}")


def test_review_actions_return_to_the_review(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _add_review(project, ReviewState.PLANNED).id

    resp = client.post(f"/reviewview/action/advance/{rid}", follow_redirects=False)

    assert resp.headers["Location"].endswith(f"/reviewview/show/{rid}")


def test_restore_returns_to_the_parent_artefact(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Restore me")
        create_version(artefact, "Initial draft")
        artefact.title = "Renamed"
        db.session.commit()
        create_version(artefact, "Edited")
        v1 = min(artefact.versions, key=lambda v: v.version_number)
        aid, vid = artefact.id, v1.id

    resp = client.post(f"/artefactversionview/action/restore/{vid}", follow_redirects=False)

    assert resp.headers["Location"].endswith(f"/artefactview/show/{aid}")


def test_project_baseline_action_returns_to_the_project(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Approved", status=RequirementState.APPROVED)
        create_version(artefact, "Initial draft")

    resp = client.post(
        f"/projectview/action/create_baseline/{project}", follow_redirects=False
    )

    assert resp.headers["Location"].endswith(f"/projectview/show/{project}")


def test_baseline_create_review_returns_to_the_baseline(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="A", status=RequirementState.APPROVED)
        create_version(artefact, "Initial draft")
        bl = Baseline(project_id=project, name="BL-1")
        db.session.add(bl)
        db.session.commit()
        snapshot_baseline(bl)
        bid = bl.id

    resp = client.post(
        f"/baselineview/action/create_review/{bid}", follow_redirects=False
    )

    assert resp.headers["Location"].endswith(f"/baselineview/show/{bid}")
    with app.app_context():
        assert db.session.query(Review).filter_by(basis_baseline_id=bid).count() == 1


# ---------------------------------------------------------------------------
# C5 — guards that would otherwise create invisible / wrong evidence
# ---------------------------------------------------------------------------
def test_comment_without_context_is_refused(client, app, project):
    _login(client)

    resp = client.post(
        "/commentview/add", data={"body": "floating", "parent": ""}, follow_redirects=True
    )

    assert resp.status_code == 200
    assert b"Choose the review or the artefact" in resp.data
    with app.app_context():
        assert db.session.query(Comment).count() == 0


def test_comment_on_an_artefact_is_still_accepted(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Comment me")
        create_version(artefact, "Initial draft")
        aid = artefact.id

    resp = client.post(
        "/commentview/add",
        data={"artefact": str(aid), "body": "needs a tolerance", "parent": ""},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    with app.app_context():
        comment = db.session.query(Comment).filter_by(artefact_id=aid).one()
        assert comment.review_id is None
        # Pinning to a version only happens for comments made under a review
        # (see tests/test_review_verdicts.py::test_ui_comment_pins_reviewed_version).
        assert comment.artefact_version_id is None


def test_verdict_on_another_project_is_refused(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        other = add_project("OTH", "Other project").id
        mine = add_artefact(project, title="In review project")
        foreign = add_artefact(other, title="Foreign artefact")
        rid, fid = _add_review(project).id, foreign.id

    resp = client.post(
        "/reviewverdictview/add",
        data={"review": str(rid), "artefact": str(fid), "decision": "APPROVE"},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"different project" in resp.data
    with app.app_context():
        assert db.session.query(ReviewVerdict).count() == 0


def test_validate_verdict_scope_is_the_shared_rule(app, project):
    """The UI hook and the service agree on what is reviewable."""
    with app.app_context():
        login_as(app)
        in_project = add_artefact(project, title="In project")
        other = add_project("P2", "Second").id
        foreign = add_artefact(other, title="Foreign")
        review = _add_review(project)
        create_version(in_project, "Initial draft")

        with pytest.raises(ValueError):
            validate_verdict_scope(review, foreign)

        # A baseline-scoped review only accepts its own artefacts.
        bl = Baseline(project_id=project, name="BL-scope")
        db.session.add(bl)
        db.session.commit()
        bl_review = create_review_from_baseline(bl)
        with pytest.raises(ValueError):
            validate_verdict_scope(bl_review, in_project)
        with pytest.raises(ValueError):
            record_verdict(bl_review, login_as(app), in_project, "APPROVE")


# ---------------------------------------------------------------------------
# C6 — hygiene: history noise, debug output, legacy ORM calls
# ---------------------------------------------------------------------------
def _edit_payload(artefact, **overrides):
    data = {
        "kind": artefact.kind.name,
        "title": artefact.title,
        "content": artefact.content,
        "status": artefact.status.name,
        "priority": artefact.priority.name,
    }
    data.update(overrides)
    return data


def test_no_op_save_creates_no_version(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(
            project,
            title="Stable requirement",
            content="The system shall hold still.",
            kind=ArtefactKind.SYSTEM_REQ,
        )
        create_version(artefact, "Initial draft")
        aid = artefact.id
        payload = _edit_payload(artefact)

    client.post(f"/artefactview/edit/{aid}", data=payload, follow_redirects=True)
    with app.app_context():
        assert db.session.query(ArtefactVersion).filter_by(artefact_id=aid).count() == 1

    # ...and a real change still versions exactly once.
    payload["title"] = "Changed requirement"
    client.post(f"/artefactview/edit/{aid}", data=payload, follow_redirects=True)
    with app.app_context():
        versions = (
            db.session.query(ArtefactVersion)
            .filter_by(artefact_id=aid)
            .order_by(ArtefactVersion.version_number)
            .all()
        )
        assert [v.version_number for v in versions] == [1, 2]
        assert versions[1].change_summary == "Edited"
        assert db.session.get(Artefact, aid).title == "Changed requirement"


def test_status_only_change_still_versions(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Lifecycle", status=RequirementState.DRAFT)
        create_version(artefact, "Initial draft")
        aid = artefact.id
        payload = _edit_payload(artefact)
    payload["status"] = RequirementState.IN_REVIEW.name

    client.post(f"/artefactview/edit/{aid}", data=payload, follow_redirects=True)
    with app.app_context():
        assert db.session.query(ArtefactVersion).filter_by(artefact_id=aid).count() == 2


def _app_sources():
    """The request-handling modules (``seed.py`` is a CLI script that logs)."""
    package = Path(app_package.__file__).parent
    return [package / "views.py", package / "services.py"]


def test_app_code_has_no_debug_prints():
    offenders = [
        p.name
        for p in _app_sources()
        if re.search(r"^\s*print\(", io.open(p, encoding="utf8").read(), re.M)
    ]
    assert offenders == []


def test_app_code_uses_session_get_not_the_legacy_query_get():
    offenders = [
        p.name
        for p in _app_sources()
        if LEGACY_QUERY_GET.search(io.open(p, encoding="utf8").read())
    ]
    assert offenders == []
