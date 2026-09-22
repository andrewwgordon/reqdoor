"""Milestone 2 regression tests — screen layout & forms (changes C11–C15).

C11  Tabs and page headers carry real names ("Versions", "Signatures") instead of
     F.A.B.'s generated "List Review Verdict" / "Show Approval".
C12  Search offers only the fields a user can reason about — no audit columns, no
     free text blobs, no to-many relationships (which F.A.B. silently ignored).
C13  Field groups are named in plain language, translated, and carry the hint that
     explains the invariant they enforce.
C14  The add forms cannot skip the workflow: artefacts start Draft, reviews Planned.
C15  The artefact list shows the reference the rest of the UI quotes, and groups an
     artefact with its own project.
"""

import re

import pytest

from app.extensions import db
from app.models import (
    Artefact,
    ArtefactKind,
    Baseline,
    ChangeRequest,
    ChangeState,
    Comment,
    Priority,
    RequirementState,
    Review,
    ReviewState,
)
from app.services import create_relationship, create_version
from app.views import (
    ApprovalView,
    ArtefactIncomingLinkView,
    ArtefactOutgoingLinkView,
    ArtefactVersionView,
    ArtefactView,
    BaselineEntryView,
    BaselineView,
    ChangeRequestView,
    ChangeSetItemView,
    CommentView,
    ProjectView,
    RelationshipTypeView,
    RelationshipView,
    ReviewAssignmentView,
    ReviewVerdictView,
    ReviewView,
    ArtefactDistributionChartView,
    ArtefactPerProjectChartView,
)
from tests.conftest import add_artefact, add_project, add_relationship_type, login_as

APP_MODEL_VIEWS = [
    ProjectView,
    ArtefactView,
    ArtefactVersionView,
    RelationshipView,
    ArtefactOutgoingLinkView,
    RelationshipTypeView,
    BaselineView,
    BaselineEntryView,
    ReviewView,
    ReviewVerdictView,
    ReviewAssignmentView,
    CommentView,
    ApprovalView,
    ChangeRequestView,
    ChangeSetItemView,
    ArtefactDistributionChartView,
    ArtefactPerProjectChartView,
]

# What each screen is allowed to be searched by (C12): nothing the user cannot
# reason about, and never an audit column, a long text blob or a collection.
EXPECTED_SEARCH = {
    "ProjectView": ["project_code", "name"],
    "ArtefactView": ["project", "kind", "title", "status", "priority"],
    "ArtefactVersionView": ["title", "kind", "status", "change_summary"],
    "RelationshipView": ["project", "source", "target", "relationship_type", "is_suspect"],
    "ArtefactOutgoingLinkView": ["relationship_type", "target", "is_suspect"],
    "RelationshipTypeView": ["display_name", "name", "is_directional"],
    "BaselineView": ["name", "project", "created_by"],
    "BaselineEntryView": ["artefact"],
    "ReviewView": ["title", "project", "status", "due_date", "created_by"],
    "ReviewVerdictView": ["review", "artefact", "reviewer", "decision"],
    "ReviewAssignmentView": ["review", "reviewer", "role_in_review"],
    "CommentView": ["review", "artefact", "body", "resolved"],
    "ApprovalView": ["review", "reviewer", "decision"],
    "ChangeRequestView": ["title", "project", "status", "priority", "requested_by"],
    "ChangeSetItemView": ["change_request", "artefact", "action", "applied"],
    "ArtefactDistributionChartView": ["project", "kind", "status"],
    "ArtefactPerProjectChartView": ["project"],
}

NEVER_SEARCHABLE = {
    "created_on",
    "changed_on",
    "changed_by",
    "content",
    "description",
    "versions",
    "comments",
    "entries",
    "items",
    "verdicts",
    "approvals",
    "assignments",
    "replies",
    "artefacts",
    "baselines",
    "reviews",
    "change_requests",
    "baseline_entries",
    "outgoing_relationships",
    "incoming_relationships",
    "basis_for_reviews",
    "parent",
    "artefact_version",
}


CHART_VIEWS = ("ArtefactDistributionChartView", "ArtefactPerProjectChartView")


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def _view(app, name):
    """The registered *instance* of a view (forms live on instances, not classes)."""
    return next(v for v in app.appbuilder.baseviews if type(v).__name__ == name)


def _search_fields(app, name):
    with app.app_context():
        form = _view(app, name).search_form()
    return [f for f in form._fields if f != "viewport"]


def _labels(body):
    """Panel headers rendered by a show or form page (text only)."""
    return [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(
            r'<(?:h3|h4) class="panel-title">(.*?)</(?:h3|h4)>', body, re.S
        )
    ]


def _accordion_labels(body):
    """The field-group headings of a show/form page, in page order."""
    return [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r'<a class="accordion-toggle"[^>]*>(.*?)</a>', body, re.S)
    ]


def _declared_titles(view):
    """The titles a user can actually reach on this view (C11)."""
    excluded = getattr(view, "exclude_route_methods", None) or set()
    attrs = ["list_title", "show_title"]
    if "add" not in excluded:
        attrs.append("add_title")
    if "edit" not in excluded:
        attrs.append("edit_title")
    return attrs


def _form_fields(body):
    return re.findall(r'<label[^>]*for="([^"]+)"', body)


# ---------------------------------------------------------------------------
# C11 — tabs and page headers speak the user's language
# ---------------------------------------------------------------------------
def test_related_view_tabs_are_named(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Tabbed artefact")
        create_version(artefact, "Initial draft")
        aid, pid = artefact.id, project
        review = Review(project_id=project, title="Tabbed review", status=ReviewState.ACTIVE)
        db.session.add(review)
        db.session.commit()
        rid = review.id
        bl = Baseline(project_id=project, name="BL-tabs")
        db.session.add(bl)
        db.session.commit()
        bid = bl.id

    artefact_page = client.get(f"/artefactview/show/{aid}").data.decode("utf-8", "replace")
    assert "Versions" in artefact_page and "Discussion" in artefact_page
    assert "Traces (out)" in artefact_page
    assert "List Artefact Version" not in artefact_page and "List Comment" not in artefact_page

    review_page = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    for tab in ("Verdicts", "Reviewers", "Discussion", "Signatures"):
        assert tab in review_page, tab
    for generated in ("List Review Verdict", "List Review Assignment", "List Approval"):
        assert generated not in review_page, generated

    project_page = client.get(f"/projectview/show/{pid}").data.decode("utf-8", "replace")
    assert all(t in project_page for t in ("Artefacts", "Baselines", "Reviews", "Change requests"))

    baseline_page = client.get(f"/baselineview/show/{bid}").data.decode("utf-8", "replace")
    assert "Contents" in baseline_page and "List Baseline Entry" not in baseline_page


def test_no_app_view_is_left_with_generated_titles(app):
    """Every reachable app screen names itself (C11).

    Titles of disabled routes (e.g. ``Edit Baseline`` on an immutable baseline)
    are never rendered, so they are not held against the view.
    """
    generated = []
    for view in app.appbuilder.baseviews:
        name = type(view).__name__
        if name not in [v.__name__ for v in APP_MODEL_VIEWS] or name in CHART_VIEWS:
            continue
        for attr in _declared_titles(view):
            title = str(getattr(view, attr, "") or "")
            if re.match(r"^(List|Show|Add|Edit) [A-Z]", title):
                generated.append(f"{name}.{attr}={title}")
    assert generated == []


# ---------------------------------------------------------------------------
# C12 — search only offers what works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("view", APP_MODEL_VIEWS, ids=lambda v: v.__name__)
def test_search_columns_are_declared_and_minimal(view):
    assert EXPECTED_SEARCH[view.__name__] == view.search_columns
    assert set(view.search_columns) & NEVER_SEARCHABLE == set()


def test_search_forms_expose_exactly_the_declared_columns(app):
    """Behavioural half of C12: the rendered search form matches the declaration."""
    for view in app.appbuilder.baseviews:
        name = type(view).__name__
        if name not in EXPECTED_SEARCH:
            continue
        assert _search_fields(app, name) == EXPECTED_SEARCH[name], name


def test_junk_filter_from_the_old_search_panels_is_not_offered(client, app, project):
    """`?_flt_0_verdicts=1` used to render a silently unfiltered list (C12)."""
    _login(client)
    with app.app_context():
        login_as(app)
        review = Review(
            project_id=project, title="Junk filter review", status=ReviewState.ACTIVE
        )
        db.session.add(review)
        db.session.commit()

    resp = client.get("/reviewview/list/?_flt_0_verdicts=999")
    assert resp.status_code == 200
    # F.A.B. refuses the column ("Filter column not allowed") and shows everything
    # unfiltered - which is exactly why it must not be offered in the search panel.
    assert b"Junk filter review" in resp.data
    assert "verdicts" not in _search_fields(app, "ReviewView")


# ---------------------------------------------------------------------------
# C13 — plain-language, translated field groups with the hint that matters
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("view", APP_MODEL_VIEWS, ids=lambda v: v.__name__)
def test_fieldset_titles_are_translatable_strings(view):
    """Raw English fieldset titles never reached the .pot file (C13)."""
    offenders = []
    for attr in ("add_fieldsets", "edit_fieldsets", "show_fieldsets"):
        for title, _spec in getattr(view, attr, None) or []:
            if isinstance(title, str):
                offenders.append(f"{view.__name__}.{attr}: {title!r}")
    assert offenders == []


def test_jargon_fieldset_renamed_on_the_artefact_pages(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Lifecycle grouping")
        create_version(artefact, "Initial draft")
        aid = artefact.id

    show = client.get(f"/artefactview/show/{aid}").data.decode("utf-8", "replace")
    assert _accordion_labels(show) == ["Artefact", "Lifecycle", "Record"]
    edit = client.get(f"/artefactview/edit/{aid}").data.decode("utf-8", "replace")
    assert _accordion_labels(edit) == ["Artefact", "Lifecycle"]


def test_forms_carry_the_hint_that_explains_the_invariant(client, app, project):
    _login(client)
    checks = {
        "/artefactview/add": "immutable new version",
        "/reviewview/add": "freeze the review",
        "/baselineview/add": "Only artefacts whose latest version is Approved",
        "/reviewverdictview/add": "review",
        "/relationshipview/add": "Where the arrow starts",
    }
    for url, hint in checks.items():
        body = client.get(url).data.decode("utf-8", "replace")
        assert hint in body, url


def test_record_groups_are_collapsed_on_show_pages(client, app, project):
    """Provenance stays available but out of the way (C13)."""
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Collapsed record")
        create_version(artefact, "Initial draft")
        aid = artefact.id

    body = client.get(f"/artefactview/show/{aid}").data.decode("utf-8", "replace")
    assert _accordion_labels(body) == ["Artefact", "Lifecycle", "Record"]
    # F.A.B. renders an open group as 'panel-collapse collapse in' and a collapsed
    # one as 'panel-collapse collapse'; the third group is 'Record'.
    groups = re.findall(r'<div id="(\d+)_href" class="panel-collapse collapse( in)?">', body)
    assert [g[0] for g in groups] == ["1", "2", "3"]
    assert groups[2][1] == "", "the Record group should start collapsed"
    assert groups[0][1] == " in", "the Artefact group should start open"


# ---------------------------------------------------------------------------
# C14 — add forms cannot skip the workflow
# ---------------------------------------------------------------------------
def test_new_artefacts_cannot_be_created_already_approved(client, app, project):
    _login(client)
    body = client.get("/artefactview/add").data.decode("utf-8", "replace")
    assert "status" not in _form_fields(body)
    assert "Add artefact (starts as Draft)" in body

    resp = client.post(
        "/artefactview/add",
        data={
            "project": str(project),
            "kind": ArtefactKind.SYSTEM_REQ.name,
            "title": "Sneaky approved",
            "content": "c",
            "priority": Priority.MEDIUM.name,
            "status": RequirementState.APPROVED.name,  # forged, not on the form
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        created = db.session.query(Artefact).filter_by(title="Sneaky approved").one()
        assert created.status == RequirementState.DRAFT


def test_new_reviews_cannot_be_created_already_approved(client, app, project):
    _login(client)
    body = client.get("/reviewview/add").data.decode("utf-8", "replace")
    assert "status" not in _form_fields(body)
    assert "Add review (starts as Planned)" in body

    client.post(
        "/reviewview/add",
        data={
            "project": str(project),
            "title": "Sneaky active review",
            "status": ReviewState.APPROVED.name,
        },
        follow_redirects=True,
    )
    with app.app_context():
        review = db.session.query(Review).filter_by(title="Sneaky active review").one()
        assert review.status == ReviewState.PLANNED


def test_edit_forms_lose_status_only_when_actions_cover_every_state(client, app, project):
    """Removing a field must never remove a door (C14 tightened by C16).

    Artefacts still edit their lifecycle because only two states have actions, so
    the review's status left the edit form once every transition became clickable.
    """
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Editable lifecycle")
        create_version(artefact, "Initial draft")
        aid = artefact.id
        review = Review(project_id=project, title="Editable state", status=ReviewState.ACTIVE)
        db.session.add(review)
        db.session.commit()
        rid = review.id

    artefact_edit = client.get(f"/artefactview/edit/{aid}").data.decode("utf-8", "replace")
    assert "status" in _form_fields(artefact_edit)

    review_edit = client.get(f"/reviewview/edit/{rid}").data.decode("utf-8", "replace")
    assert "status" not in _form_fields(review_edit)
    review_show = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    for button in ("advance", "request_changes", "sign_off", "sign_dissent"):
        assert button in review_show, button


def test_fieldsets_are_the_only_source_of_form_fields(client, app, project):
    """Where a fieldset exists, the duplicate *_columns lists were dropped (C14)."""
    with app.app_context():
        for view_class, attr, fieldset in (
            (ArtefactView, "add_columns", "add_fieldsets"),
            (ArtefactView, "edit_columns", "edit_fieldsets"),
            (ArtefactView, "show_columns", "show_fieldsets"),
            (ReviewView, "add_columns", "add_fieldsets"),
            (ReviewView, "show_columns", "show_fieldsets"),
            (CommentView, "add_columns", "add_fieldsets"),
            (ReviewVerdictView, "show_columns", "show_fieldsets"),
            (ChangeRequestView, "show_columns", "show_fieldsets"),
            (BaselineView, "show_columns", "show_fieldsets"),
        ):
            view = view_class()
            derived = getattr(view, attr)
            flat = [f for _t, spec in getattr(view_class, fieldset) for f in spec["fields"]]
            assert derived == flat, f"{view_class.__name__}.{attr}"


# ---------------------------------------------------------------------------
# C15 — the artefact list is findable
# ---------------------------------------------------------------------------
def test_artefact_list_shows_the_reference(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        add_artefact(project, title="Reference column")

    body = client.get("/artefactview/list/").data.decode("utf-8", "replace")
    assert "Ref" in _labels(body) or "Ref" in body
    assert "/artefactview/list/?_oc_ArtefactView=id" in body  # and it can be ordered


def test_artefact_list_groups_each_projects_artefacts(client, app):
    """base_order was 'title', which interleaved every project alphabetically."""
    _login(client)
    with app.app_context():
        login_as(app)
        first = add_project("AAA", "First").id
        second = add_project("BBB", "Second").id
        add_artefact(first, title="Zulu in first project")
        add_artefact(first, title="Mike in first project")
        add_artefact(second, title="Alpha in second project")

    body = client.get("/artefactview/list/").data.decode("utf-8", "replace")
    order = re.findall(r"Zulu in first project|Mike in first project|Alpha in second project", body)
    assert order == [
        "Zulu in first project",
        "Mike in first project",
        "Alpha in second project",
    ]


def test_row_views_page_capped_at_25_rows():
    """Every row-based view declares the 25-row page cap (no >25 pages)."""
    from app.views import PAGE_SIZE

    row_views = [
        ProjectView,
        ArtefactView,
        ArtefactVersionView,
        RelationshipTypeView,
        ArtefactOutgoingLinkView,
        ArtefactIncomingLinkView,
        RelationshipView,
        ReviewView,
        ReviewVerdictView,
        ReviewAssignmentView,
        CommentView,
        ApprovalView,
        ChangeRequestView,
        ChangeSetItemView,
        BaselineEntryView,
        BaselineView,
    ]
    assert PAGE_SIZE == 25
    assert all(view.page_size == PAGE_SIZE for view in row_views)


def test_artefact_list_default_order_declared():
    assert ArtefactView.base_order == ("project_id", "asc")
    assert ArtefactView.list_columns[0] == "id"


# ---------------------------------------------------------------------------
# readable cells: no None, no True/False, no microsecond timestamps
# ---------------------------------------------------------------------------
STAMP = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3,}")


def _tds(body):
    return [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td>(.*?)</td>", body, re.S)]


@pytest.mark.parametrize(
    "url",
    [
        "/projectview/list/",
        "/artefactview/list/",
        "/reviewview/list/",
        "/changerequestview/list/",
        "/baselineview/list/",
        "/relationshipview/list/",
        "/relationshiptypeview/list/",
        "/commentview/list/",
    ],
)
def test_list_pages_do_not_leak_storage_values(client, app, project, url):
    """Empty, boolean and datetime cells read like people wrote them (C13/C15)."""
    _login(client)
    with app.app_context():
        user = login_as(app)
        source = add_artefact(project, title="Readable cell artefact")
        target = add_artefact(project, title="Trace target artefact")
        create_version(source, "Initial draft")
        create_version(target, "Initial draft")
        rel_type = add_relationship_type("READS", "Reads")
        create_relationship(source, target, rel_type)
        review = Review(project_id=project, title="Readable review")
        db.session.add(review)
        db.session.commit()
        db.session.add(
            ChangeRequest(
                project_id=project,
                title="Readable change",
                status=ChangeState.SUBMITTED,
                requested_by=user,
            )
        )
        db.session.add(Baseline(project_id=project, name="BL-readable"))
        db.session.add(
            Comment(review=review, artefact=source, body="Needs a tolerance band")
        )
        db.session.commit()

    body = client.get(url).data.decode("utf-8", "replace")
    cells = _tds(body)
    assert cells, url
    assert "None" not in cells, (url, cells)
    assert "True" not in cells and "False" not in cells, (url, cells)
    assert not [c for c in cells if STAMP.search(c)], (url, cells)


def test_review_cells_explain_what_is_missing(client, app, project):
    """A review without a basis or reviewers says so, instead of showing blanks."""
    _login(client)
    with app.app_context():
        login_as(app)
        review = Review(project_id=project, title="Sparse review", status=ReviewState.PLANNED)
        db.session.add(review)
        db.session.commit()
        rid = review.id

    show = client.get(f"/reviewview/show/{rid}").data.decode("utf-8", "replace")
    assert "Live project content" in show
    assert "Not started - no reviewers yet" in show
    assert "None" not in _tds(show)

    listing = client.get("/reviewview/list/").data.decode("utf-8", "replace")
    assert "Not started - no reviewers yet" in _tds(listing)


def test_suspect_and_direction_flags_are_worded(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        source = add_artefact(project, title="Suspect source")
        target = add_artefact(project, title="Suspect target")
        create_version(source, "Initial draft")
        create_version(target, "Initial draft")
        rel_type = add_relationship_type("READS", "Reads")
        link = create_relationship(source, target, rel_type)
        link.is_suspect = True
        db.session.commit()
    cells = _tds(client.get("/relationshipview/list/").data.decode("utf-8", "replace"))
    assert "Suspect - re-check" in cells
    type_cells = _tds(
        client.get("/relationshiptypeview/list/").data.decode("utf-8", "replace")
    )
    assert "One way" in type_cells


# ---------------------------------------------------------------------------
# labels: the screens stop quoting the schema back at the user
# ---------------------------------------------------------------------------
def test_column_labels_are_renamed(client, app, project):
    _login(client)
    with app.app_context():
        login_as(app)
        artefact = add_artefact(project, title="Labelled", kind=ArtefactKind.SYSTEM_REQ)
        create_version(artefact, "Initial draft")
        aid = artefact.id

    body = client.get(f"/artefactview/show/{aid}").data.decode("utf-8", "replace")
    for friendly in ("Type", "Lifecycle", "Last changed"):
        assert friendly in body, friendly
    for schema in ("Changed On", "Created On", ">Kind<"):
        assert schema not in body, schema


def test_every_app_view_names_its_own_screens(app):
    """Charts are headed by ``chart_title``; every CRUD screen names itself (C11)."""
    unnamed = []
    for view in app.appbuilder.baseviews:
        name = type(view).__name__
        if name not in [v.__name__ for v in APP_MODEL_VIEWS] or name in CHART_VIEWS:
            continue
        for attr in _declared_titles(view):
            if not str(getattr(view, attr, "") or ""):
                unnamed.append(f"{name}.{attr}")
    assert unnamed == []
