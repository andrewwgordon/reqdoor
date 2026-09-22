"""Milestone 3 regression tests — workflow actions (changes C16–C20).

C16  One state-aware "Next step" button replaces Start/Approve/Close (reviews) and
     Analyse/Approve/Implement/Verify/Close (change requests).
C17  One click signs a review with the signed-in user's identity; signatures are
     evidence, not a form to fill in.
C18  Adding from a parent's tab carries the parent with it (no wrong-object picks).
C19  Both baseline doors name, explain and refuse identically.
C20  Deletion protection says why, and *Mark obsolete* gives you the door instead.
"""

import re

import pytest

from app.extensions import db
from app.models import (
    Approval,
    Artefact,
    Baseline,
    BaselineEntry,
    ChangeRequest,
    ChangeState,
    Comment,
    Project,
    Decision,
    RequirementState,
    Review,
    ReviewState,
    ReviewVerdict,
)
from app.services import (
    CHANGE_ADVANCE,
    REVIEW_ADVANCE,
    REVIEW_REQUEST_CHANGES,
    REVIEW_TRANSITIONS,
    CHANGE_TRANSITIONS,
    create_version,
    default_baseline_name,
)
from tests.conftest import (
    add_artefact,
    add_project,
    add_relationship_type,
    login_as,
)


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def _review(project, status=ReviewState.PLANNED):
    review = Review(project_id=project, title=f"Review {status.value}", status=status)
    db.session.add(review)
    db.session.commit()
    return review


def _change_request(project, user, status=ChangeState.SUBMITTED):
    cr = ChangeRequest(
        project_id=project, title=f"CR {status.value}", status=status, requested_by=user
    )
    db.session.add(cr)
    db.session.commit()
    return cr


def _approved_artefact(project, title):
    artefact = add_artefact(project, title=title, status=RequirementState.APPROVED)
    create_version(artefact, "Initial draft")
    return artefact


# ---------------------------------------------------------------------------
# C16 — one button per legal move, defined by the workflow tables
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "advance_map,table",
    [(REVIEW_ADVANCE, REVIEW_TRANSITIONS), (CHANGE_ADVANCE, CHANGE_TRANSITIONS)],
)
def test_advance_paths_only_follow_legal_transitions(advance_map, table):
    """The 'Next step' map can never drift away from the transition table."""
    for current, target in advance_map.items():
        if target is None:
            assert table[current] == set(), f"{current} is finished but has successors"
            continue
        assert target in table[current], f"{current} -> {target} is not legal"


def test_request_changes_branch_is_legal():
    for current, target in REVIEW_REQUEST_CHANGES.items():
        assert target in REVIEW_TRANSITIONS[current]


def test_change_request_offers_exactly_one_workflow_button(client, app, project):
    """C16: five always-visible buttons, four of which could only fail."""
    _login(client)
    with app.app_context():
        user = login_as(app)
        crid = _change_request(project, user).id

    body = client.get(f"/changerequestview/show/{crid}").data.decode("utf-8", "replace")
    actions = set(re.findall(r"/changerequestview/action/(\w+)/", body))
    assert actions == {"advance"}, actions
    assert b"Submitted - Analysed - Approved" in client.get(
        f"/changerequestview/show/{crid}"
    ).data


def test_review_offers_the_two_paths_and_the_two_signatures(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _review(project).id

    body = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    actions = set(re.findall(r"/reviewview/action/(\w+)/", body))
    assert actions == {"advance", "request_changes", "sign_off", "sign_dissent"}, actions


def test_old_workflow_action_names_are_gone(client, app, project):
    """A stale link to a removed button gets a warning, never a 500.

    C16 renamed these actions, so bookmarks and half-finished clicks will keep
    pointing at the old names for a while.
    """
    _login(client)
    with app.app_context():
        user = login_as(app)
        rid = _review(project, ReviewState.ACTIVE).id
        crid = _change_request(project, user).id

    for url in (
        f"/reviewview/action/activate/{rid}",
        f"/reviewview/action/approve_review/{rid}",
        f"/reviewview/action/close_review/{rid}",
        f"/changerequestview/action/analyse/{crid}",
        f"/changerequestview/action/verify/{crid}",
    ):
        resp = client.post(url, follow_redirects=True)
        assert resp.status_code == 200, url
        assert b"not an action on this screen" in resp.data, url
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.ACTIVE
        assert db.session.get(ChangeRequest, crid).status == ChangeState.SUBMITTED


def test_next_step_is_refused_once_the_record_is_closed(client, app, project):
    _login(client)
    with app.app_context():
        user = login_as(app)
        rid = _review(project, ReviewState.CLOSED).id
        crid = _change_request(project, user, status=ChangeState.CLOSED).id

    for url, needle in (
        (f"/reviewview/action/advance/{rid}", b"Nothing left to do"),
        (f"/changerequestview/action/advance/{crid}", b"Nothing left to do"),
    ):
        resp = client.post(url, follow_redirects=True)
        assert resp.status_code == 200
        assert needle in resp.data
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.CLOSED
        assert db.session.get(ChangeRequest, crid).status == ChangeState.CLOSED


# ---------------------------------------------------------------------------
# C17 — one-click electronic signature
# ---------------------------------------------------------------------------
def test_signing_uses_the_logged_in_user_and_state(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        active = _review(project, ReviewState.ACTIVE).id
        planned = _review(project, ReviewState.PLANNED).id
        closed = _review(project, ReviewState.CLOSED).id

    for rid in (planned, closed):
        resp = client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
        assert b"can be signed" in resp.data
        with app.app_context():
            assert db.session.query(Approval).filter_by(review_id=rid).count() == 0

    client.post(f"/reviewview/action/sign_off/{active}", follow_redirects=True)
    with app.app_context():
        approval = db.session.query(Approval).filter_by(review_id=active).one()
        assert approval.decision == Decision.APPROVE
        assert approval.reviewer.username == "admin"
        assert approval.review_id == active


def test_re_signing_replaces_my_own_position(client, app, project):
    """A double click must not stack up contradictory signatures."""
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _review(project, ReviewState.COMMENT_RESOLUTION).id

    client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
    client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
    client.post(f"/reviewview/action/sign_dissent/{rid}", follow_redirects=True)
    with app.app_context():
        approvals = db.session.query(Approval).filter_by(review_id=rid).all()
        assert len(approvals) == 1
        assert approvals[0].decision == Decision.REJECT


def test_signing_does_not_move_the_review(client, app, project):
    """A signature is a statement; the chair still decides with 'Next step'."""
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _review(project, ReviewState.ACTIVE).id

    client.post(f"/reviewview/action/sign_off/{rid}", follow_redirects=True)
    with app.app_context():
        assert db.session.get(Review, rid).status == ReviewState.ACTIVE


def test_signatures_are_read_only_evidence(client, app, project):
    """ApprovalView lost its add/edit routes to the buttons (C17)."""
    _login(client)
    for url in ("/approvalview/add", "/approvalview/edit/1", "/approvalview/delete/1"):
        assert client.get(url).status_code == 404, url
    assert client.get("/approvalview/list/").status_code == 200


# ---------------------------------------------------------------------------
# C18 — adding from a parent's tab carries the parent along
# ---------------------------------------------------------------------------
def test_tab_add_links_carry_the_parent(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _review(project).id

    body = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    assert f"/commentview/add?_flt_0_review={rid}" in body
    assert f"/reviewverdictview/add?_flt_0_review={rid}" in body


def test_comment_from_the_review_tab_needs_only_its_own_text(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        rid = _review(project).id

    form = client.get(f"/commentview/add?_flt_0_review={rid}").data.decode("utf-8", "replace")
    assert 'for="review"' not in form  # the parent is not a choice to make

    resp = client.post(
        f"/commentview/add?_flt_0_review={rid}",
        data={"body": "Prefilled from the tab", "parent": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        comment = db.session.query(Comment).one()
        assert comment.review_id == rid
        assert comment.artefact_id is None


def test_verdict_from_the_review_tab_scopes_itself(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = _approved_artefact(project, "Verdict target")
        rid, aid = _review(project).id, artefact.id

    resp = client.post(
        f"/reviewverdictview/add?_flt_0_review={rid}",
        data={"artefact": str(aid), "decision": "APPROVE", "comment": "fine"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        verdict = db.session.query(ReviewVerdict).one()
        assert (verdict.review_id, verdict.artefact_id) == (rid, aid)
        assert verdict.reviewer.username == "admin"
        assert verdict.artefact_version_id is not None  # pinned to what was reviewed


def test_out_of_scope_verdict_is_still_refused_from_the_tab(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        other = add_project("OTH", "Other").id
        foreign = add_artefact(other, title="Foreign artefact")
        create_version(foreign, "Initial draft")
        rid, fid = _review(project).id, foreign.id

    resp = client.post(
        f"/reviewverdictview/add?_flt_0_review={rid}",
        data={"artefact": str(fid), "decision": "APPROVE"},
        follow_redirects=True,
    )
    assert b"different project" in resp.data
    with app.app_context():
        assert db.session.query(ReviewVerdict).count() == 0


# ---------------------------------------------------------------------------
# C19 — the two baseline doors behave the same
# ---------------------------------------------------------------------------
def _flash_text(resp):
    return re.sub(r"\s+", " ", resp.data.decode("utf-8", "replace"))


def test_blank_baseline_name_is_date_stamped_like_the_project_door(client, app):
    _login(client)
    with app.app_context():
        login_as(app)
        project = add_project("NAM", "Naming project").id
        _approved_artefact(project, "Approved one")

    resp = client.post(
        "/baselineview/add", data={"project": str(project), "name": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        created = db.session.query(Baseline).one()
        assert created.name == default_baseline_name(db.session.get(Project, project))
        assert re.match(r"^NAM BL \d{4}-\d{2}-\d{2}$", created.name)
        assert len(created.entries) == 1


def test_both_doors_refuse_an_empty_baseline_the_same_way(client, app):
    _login(client)
    with app.app_context():
        login_as(app)
        project = add_project("EMPTY", "Empty project").id
        add_artefact(project, title="Only a draft")  # never approved

    via_add = client.post(
        "/baselineview/add", data={"project": str(project), "name": "Nope"},
        follow_redirects=True,
    )
    via_action = client.post(
        f"/projectview/action/create_baseline/{project}", follow_redirects=True
    )
    for resp in (via_add, via_action):
        text = _flash_text(resp)
        assert "Nothing to baseline" in text, text[:300]
    with app.app_context():
        assert db.session.query(Baseline).count() == 0  # neither door leaves debris


def test_partial_baseline_says_what_was_skipped(client, app):
    _login(client)
    with app.app_context():
        login_as(app)
        project = add_project("PART", "Partial project").id
        _approved_artefact(project, "Ready requirement")
        add_artefact(project, title="Still a draft requirement")

    resp = client.post(
        f"/projectview/action/create_baseline/{project}", follow_redirects=True
    )
    text = _flash_text(resp)
    assert "captured 1 approved artefact version" in text
    assert "skipped" in text and "Still a draft requirement" in text
    with app.app_context():
        baseline = db.session.query(Baseline).one()
        assert len(baseline.entries) == 1


# ---------------------------------------------------------------------------
# C20 — deletion protection with a way out
# ---------------------------------------------------------------------------
def test_delete_guard_names_the_reason_and_the_alternative(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = _approved_artefact(project, "Baselined artefact")
        baseline = Baseline(project_id=project, name="BL-GUARD")
        db.session.add(baseline)
        db.session.commit()
        from app.services import snapshot_baseline

        snapshot_baseline(baseline)
        aid = artefact.id

    resp = client.get(f"/artefactview/delete/{aid}", follow_redirects=True)
    text = _flash_text(resp)
    assert "cannot be deleted because it is frozen in the baseline(s) BL-GUARD" in text
    assert "Mark obsolete" in text
    with app.app_context():
        assert db.session.get(Artefact, aid) is not None


def test_delete_guard_names_trace_links(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        source = add_artefact(project, title="Link source")
        target = add_artefact(project, title="Link target")
        rel_type = add_relationship_type("REFINES", "Refines")
        create_version(source, "Initial draft")
        create_version(target, "Initial draft")
        from app.services import create_relationship

        create_relationship(source, target, rel_type)
        tid = target.id

    text = _flash_text(client.get(f"/artefactview/delete/{tid}", follow_redirects=True))
    assert "it has 1 trace link(s)" in text
    with app.app_context():
        assert db.session.get(Artefact, tid) is not None


def test_mark_obsolete_retires_without_destroying_evidence(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = _approved_artefact(project, "Obsolete soon")
        aid = artefact.id
        before = len(artefact.versions)

    resp = client.post(f"/artefactview/action/obsolete/{aid}", follow_redirects=False)
    assert resp.headers["Location"].endswith(f"/artefactview/show/{aid}")
    with app.app_context():
        artefact = db.session.get(Artefact, aid)
        assert artefact.status == RequirementState.OBSOLETE
        assert len(artefact.versions) == before + 1
        assert artefact.versions[-1].change_summary == "Marked obsolete"

    # ...and pressing it again is a warning, not a duplicate version.
    versions = None
    with app.app_context():
        versions = len(db.session.get(Artefact, aid).versions)
    client.post(f"/artefactview/action/obsolete/{aid}", follow_redirects=True)
    with app.app_context():
        assert len(db.session.get(Artefact, aid).versions) == versions


def test_obsolete_artefact_still_protects_its_baseline(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = _approved_artefact(project, "Frozen then retired")
        baseline = Baseline(project_id=project, name="BL-OBS")
        db.session.add(baseline)
        db.session.commit()
        from app.services import snapshot_baseline

        snapshot_baseline(baseline)
        aid = artefact.id

    client.post(f"/artefactview/action/obsolete/{aid}", follow_redirects=True)
    text = _flash_text(client.get(f"/artefactview/delete/{aid}", follow_redirects=True))
    assert "cannot be deleted" in text
    with app.app_context():
        assert db.session.get(Artefact, aid) is not None
        assert db.session.query(BaselineEntry).filter_by(artefact_id=aid).count() == 1

