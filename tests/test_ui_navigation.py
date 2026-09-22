"""Milestone 1 regression tests — navigation (changes C7–C10).

C7  One project screen: the duplicate two-pane explorer is gone and the view is
    named after what it is (``ProjectView`` → ``/projectview/…``).
C8  The menu reads as the governance loop: Requirements → Governance → Analysis →
    Dashboard → Administration (was 6 categories in registration order).
C9  Saved-filter menu entries (Draft artefacts, Active reviews, Open change
    requests, Suspect trace links, Orphan report) are real one-click views.
C10 The landing page shows the loop and live counts instead of the stock welcome.
"""

import re

from app.extensions import db
from app.models import (
    ChangeRequest,
    ChangeState,
    Priority,
    RequirementState,
    Review,
    ReviewState,
)
from app.services import create_relationship, create_version
from app.views import register_views
from tests.conftest import (
    add_artefact,
    add_project,
    add_relationship_type,
    login_as,
)

MY_CATEGORIES = [
    "Requirements",
    "Governance",
    "Analysis",
    "Dashboard",
    "Administration",
]

EXPECTED_MENU = [
    ("Requirements", ["Projects", "Artefacts", "Trace Links", "Draft artefacts"]),
    (
        "Governance",
        [
            "Baselines",
            "Reviews",
            "Active reviews",
            "Change Requests",
            "Open change requests",
        ],
    ),
    (
        "Analysis",
        ["Traceability Graph", "Impact Report", "Suspect trace links", "Orphan report"],
    ),
    ("Dashboard", ["Artefact Mix", "Artefacts per Project"]),
    ("Administration", ["Link Types"]),
]


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def _menu_tree(appbuilder):
    """[(category, [entry names in menu order]), ...] for the live menu tree."""
    return [
        (item.name, [child.name for child in item.childs])
        for item in appbuilder.menu.get_list()
    ]


def _rows(body, view):
    """Number of data rows on a list page (one show-link per row)."""
    return len(re.findall(rf"/{re.escape(view)}/show/", body))


def _seed_workload(app):
    """One of each interesting state, so filter results are unambiguous."""
    with app.app_context():
        user = login_as(app)
        project = add_project("FLT", "Filter project").id
        draft = add_artefact(
            project, title="DraftArtefact", status=RequirementState.DRAFT
        )
        approved = add_artefact(
            project, title="ApprovedArtefact", status=RequirementState.APPROVED
        )
        traced = add_artefact(
            project, title="TracedArtefact", status=RequirementState.IN_REVIEW
        )
        for artefact in (draft, approved, traced):
            create_version(artefact, "Initial draft")
        rel_type = add_relationship_type("SATISFIES", "Satisfies")
        create_relationship(approved, traced, rel_type)  # a clean trace link
        suspect = create_relationship(draft, traced, rel_type)
        suspect.is_suspect = True  # what an edit to the source would have done
        db.session.commit()
        for status in (ReviewState.ACTIVE, ReviewState.CLOSED):
            db.session.add(
                Review(project_id=project, title=f"Review_{status.value}", status=status)
            )
        for status in (ChangeState.SUBMITTED, ChangeState.CLOSED):
            db.session.add(
                ChangeRequest(
                    project_id=project,
                    title=f"CR_{status.value}",
                    status=status,
                    priority=Priority.MEDIUM,
                    requested_by=user,
                )
            )
        db.session.commit()
        return project


# ---------------------------------------------------------------------------
# C7 — one project screen
# ---------------------------------------------------------------------------
def test_the_menu_offers_exactly_one_project_screen(app):
    entries = [
        name for _cat, children in _menu_tree(app.appbuilder) for name in children
    ]
    screens = [e for e in entries if "Project" in e and e != "Artefacts per Project"]
    assert screens == ["Projects"]


def test_project_screen_url_and_action_endpoints_are_the_new_ones(client, app, project):
    _login(client)
    assert client.get("/projectview/list/").status_code == 200
    assert client.get(f"/projectview/show/{project}").status_code == 200
    assert client.get("/projectmasterview/list/").status_code == 404
    assert client.get("/projectworkspaceview/list/").status_code == 404


def test_renamed_view_leaves_no_stale_permissions(app):
    """Registering views prunes permissions left behind by the rename (C7/C8)."""
    sm = app.appbuilder.sm
    with app.app_context():
        sm.add_permission_view_menu("can_list", "ProjectMasterView")
        assert sm.find_permission_view_menu("can_list", "ProjectMasterView")

        register_views(app.appbuilder)

        assert sm.find_permission_view_menu("can_list", "ProjectMasterView") is None
        # ...and live permissions survive the sweep.
        assert sm.find_permission_view_menu("can_list", "ProjectView")
        assert sm.find_permission_view_menu("menu_access", "Draft artefacts")


# ---------------------------------------------------------------------------
# C8 — menu order & grouping
# ---------------------------------------------------------------------------
def test_menu_categories_replace_the_old_silo_order(app):
    categories = [name for name, _children in _menu_tree(app.appbuilder)]
    assert [c for c in categories if c != "Security"] == MY_CATEGORIES


def test_menu_reads_as_the_governance_loop(app):
    tree = dict(_menu_tree(app.appbuilder))
    for category, expected in EXPECTED_MENU:
        assert tree[category] == expected, category


def test_every_menu_entry_is_labelled_and_iconed(app):
    for category, _expected in EXPECTED_MENU:
        for child in app.appbuilder.menu.find(category).childs:
            assert child.label, f"{category}/{child.name} has no label"
            assert child.icon, f"{category}/{child.name} has no icon"


def test_saved_filter_entries_point_at_list_urls(app):
    with app.test_request_context():
        link = app.appbuilder.menu.find("Suspect trace links")
        assert link.href.endswith("/relationshipview/list/?_flt_0_is_suspect=1")
        assert app.appbuilder.menu.find("Orphan report").href == "/impactview/orphans/"
        # View entries resolve their href from their own endpoint, not a literal.
        assert app.appbuilder.menu.find("Projects").get_url().endswith("/projectview/list/")


# ---------------------------------------------------------------------------
# C9 — saved filters really filter
# ---------------------------------------------------------------------------
def test_saved_filters_return_the_expected_rows(client, app):
    _login(client)
    _seed_workload(app)

    cases = [
        ("/artefactview/list/?_flt_0_status=DRAFT", "artefactview", 1),
        ("/artefactview/list/?_flt_0_status=APPROVED", "artefactview", 1),
        ("/artefactview/list/", "artefactview", 3),
        ("/reviewview/list/?_flt_0_status=ACTIVE", "reviewview", 1),
        ("/reviewview/list/", "reviewview", 2),
        ("/changerequestview/list/?_flt_0_status=SUBMITTED", "changerequestview", 1),
        ("/changerequestview/list/", "changerequestview", 2),
        ("/relationshipview/list/?_flt_0_is_suspect=1", "relationshipview", 1),
        ("/relationshipview/list/?_flt_0_is_suspect=0", "relationshipview", 1),
        ("/relationshipview/list/", "relationshipview", 2),
    ]
    for url, view, expected in cases:
        body = client.get(url).data.decode("utf-8", "replace")
        assert _rows(body, view) == expected, url


def test_menu_hrefs_all_resolve(client, app):
    """No dead doors: every leaf in the app menu answers (C8/C9 typo guard)."""
    _login(client)
    with app.test_request_context():
        hrefs = [
            (child.name, child.get_url())
            for category in MY_CATEGORIES
            for child in app.appbuilder.menu.find(category).childs
        ]
    checked = 0
    for name, href in hrefs:
        assert href, name
        resp = client.get(href, follow_redirects=False)
        assert resp.status_code in (200, 302), f"{name} -> {href}"
        checked += 1
    assert checked == sum(len(entries) for _c, entries in EXPECTED_MENU)


def test_orphan_report_lists_only_untraced_artefacts(client, app):
    _login(client)
    _seed_workload(app)
    body = client.get("/impactview/orphans/").data.decode("utf-8", "replace")
    assert "Filter project" in body
    assert "ApprovedArtefact" not in body  # traced, so not an orphan


# ---------------------------------------------------------------------------
# C10 — landing page
# ---------------------------------------------------------------------------
def test_landing_page_replaces_the_stock_welcome(client, app):
    body = client.get("/").data.decode("utf-8", "replace")
    assert "Requirements governance at a glance" in body
    assert "<center>Welcome" not in body


def test_landing_page_counts_are_live(client, app):
    _seed_workload(app)
    body = client.get("/").data.decode("utf-8", "replace")

    tiles = {
        label: int(value)
        for value, label in re.findall(
            r'font-weight:bold;">(\d+)</span>\s*<span>([^<]+)</span>', body
        )
    }
    assert tiles == {
        "Projects": 1,
        "Artefacts": 3,
        "Draft": 1,
        "Approved": 1,
        "Baselines": 0,
        "Open reviews": 1,
        "Open changes": 1,
        "Suspect links": 1,
        "Untraced artefacts": 0,
    }


def test_landing_page_links_lead_into_the_loop(client, app):
    body = client.get("/").data.decode("utf-8", "replace")
    for href in (
        "/projectview/list/",
        "/artefactview/add",
        "/relationshipview/add",
        "/baselineview/list/",
        "/reviewview/list/",
        "/reviewview/list/?_flt_0_status=ACTIVE",
        "/tracegraphview/",
        "/impactview/",
        "/changerequestview/list/",
        "/relationshipview/list/?_flt_0_is_suspect=1",
        "/impactview/orphans/",
    ):
        assert href in body, href


def test_landing_page_stays_public_before_login(client, app):
    """The stock index is public; the replacement must not break the login path."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert b"/login/" in resp.data or b"Sign In" in resp.data
