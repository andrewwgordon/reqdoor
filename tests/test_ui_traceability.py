"""Milestone 4 regression tests — traceability & analysis usability (C21–C24).

C21  Impact and orphan reports are clickable: every row leads to the artefact, the
     graph or the link itself, and the traversal depth is a control, not a constant.
C22  The analysis pickers are project-scoped and capped — they no longer load every
     artefact in the database into one <select>.
C23  An artefact page shows both ends of a trace link (Traces (in) / Traces (out)).
C24  'Add trace link from here' starts the link with this artefact already pinned.
"""

import re

import pytest

from app.extensions import db
from app.models import (
    Artefact,
    ArtefactKind,
    ArtefactRelationship,
    Priority,
    RequirementState,
)
from app.services import create_relationship, create_version
from app.views import (
    ArtefactIncomingLinkView,
    ArtefactOutgoingLinkView,
    PICKER_LIMIT,
)
from tests.conftest import add_artefact, add_project, add_relationship_type, login_as


@pytest.fixture
def chain(client, app):
    """One project holding A -> B -> C -> D, plus an unrelated project.

    Returns {label: artefact id} plus 'other_project' and 'link' ids.
    """
    with app.app_context():
        user = login_as(app)
        project = add_project("TRC", "Trace project").id
        other = add_project("OTH", "Other project").id
        labels = ("A", "B", "C", "D")
        made = {}
        for label in labels:
            made[label] = add_artefact(
                project, title=f"Artefact {label}", status=RequirementState.APPROVED
            )
        for artefact in made.values():
            create_version(artefact, "Initial draft")
        add_artefact(other, title="Foreign artefact")
        rel_type = add_relationship_type("CHAINS", "Chains to")
        links = {}
        for source, target in zip(labels, labels[1:]):
            link = create_relationship(made[source], made[target], rel_type)
            links[f"{source}{target}"] = link.id
        # make one link suspect, as an edit to its source would
        db.session.get(ArtefactRelationship, links["AB"]).is_suspect = True
        db.session.commit()
        ids = {label: artefact.id for label, artefact in made.items()}
        ids.update(
            project=project, other=other, link_ab=links["AB"], rel_type=rel_type.id
        )
    _login(client)
    return ids


def _login(client):
    resp = client.post(
        "/login/", data={"username": "admin", "password": "admin"}, follow_redirects=True
    )
    assert resp.status_code == 200


def _body(client, url):
    """A page body, with an accidental redirect to the login page caught early."""
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp.data.decode("utf-8", "replace")


def _tab_cells(html, anchor):
    """Data cells rendered inside one related-view tab of a show page."""
    parts = re.split(
        r'id="(ArtefactVersionView|ArtefactOutgoingLinkView|ArtefactIncomingLinkView|CommentView)"',
        html,
    )
    for name, body in zip(parts[1::2], parts[2::2]):
        if name == anchor:
            return [
                re.sub(r"<[^>]+>", " ", cell).strip()
                for cell in re.findall(r"<td>(.*?)</td>", body[:20000], re.S)
            ]
    return None


def _options(html, select_id):
    """Labels of the <option>s inside a given <select>."""
    match = re.search(rf'<select[^>]*id="{select_id}".*?>(.*?)</select>', html, re.S)
    if not match:
        return None
    return [
        re.sub(r"\s+", " ", text).strip()
        for text in re.findall(r"<option[^>]*>(.*?)</option>", match.group(1), re.S)
    ]


# ---------------------------------------------------------------------------
# C22 — project-scoped, capped pickers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["/tracegraphview/", "/impactview/"])
def test_picker_shows_no_artefacts_before_a_project_is_chosen(client, app, chain, url):
    html = _body(client, url)
    assert _options(html, "pk") is None  # the artefact select does not exist yet
    assert "Artefact A" not in html
    assert "Pick a project above" in html
    assert _options(html, "project") and len(_options(html, "project")) == 3  # + placeholder


@pytest.mark.parametrize("url", ["/tracegraphview/", "/impactview/"])
def test_picker_lists_only_the_chosen_projects_artefacts(client, app, chain, url):
    html = _body(client, f"{url}?project={chain['project']}")
    options = _options(html, "pk")
    assert any("Artefact A" in option for option in options)
    assert all("Foreign artefact" not in option for option in options)


def test_picker_is_capped_and_says_so(client, app):
    _login(client)
    with app.app_context():
        login_as(app)
        project = add_project("BIG", "Big project").id
        db.session.add_all(
            [
                Artefact(
                    project_id=project,
                    kind=ArtefactKind.SYSTEM_REQ,
                    title=f"Bulk artefact {n}",
                    content="c",
                    status=RequirementState.DRAFT,
                    priority=Priority.MEDIUM,
                )
                for n in range(PICKER_LIMIT + 5)
            ]
        )
        db.session.commit()

    html = _body(client, f"/tracegraphview/?project={project}")
    assert len(_options(html, "pk")) == PICKER_LIMIT
    assert "Showing the first" in html


def test_picker_still_honours_a_direct_artefact_link(client, app, chain):
    """The Milestone 0 behaviour (?pk= jumps straight in) must survive."""
    for url, target in (
        (f"/tracegraphview/?pk={chain['A']}", "/tracegraphview/show/"),
        (f"/impactview/?pk={chain['A']}", "/impactview/analyse/"),
    ):
        resp = client.get(url, follow_redirects=False)
        assert resp.status_code == 302
        assert target in resp.headers["Location"]


def test_result_pages_picker_starts_on_the_artefacts_project(client, app, chain):
    graph = _body(client, f"/tracegraphview/show/{chain['A']}/")
    impact = _body(client, f"/impactview/analyse/{chain['A']}/")
    for html in (graph, impact):
        assert f'<option value="{chain["project"]}" selected' in html
        assert any("Artefact B" in option for option in _options(html, "pk"))


# ---------------------------------------------------------------------------
# C23 — both ends of a link on the artefact page
# ---------------------------------------------------------------------------
def test_artefact_page_shows_incoming_and_outgoing_separately(client, app, chain):
    source_page = _body(client, f"/artefactview/show/{chain['A']}")
    target_page = _body(client, f"/artefactview/show/{chain['B']}")

    # The chain is A -> B -> C -> D, so A has only outgoing rows and B sees the
    # link that arrives *and* the one it starts.
    assert _tab_cells(source_page, "ArtefactOutgoingLinkView")  # A -> B
    assert "Artefact B" in " ".join(_tab_cells(source_page, "ArtefactOutgoingLinkView"))
    assert not _tab_cells(source_page, "ArtefactIncomingLinkView")  # nothing arrives at A
    incoming = " ".join(_tab_cells(target_page, "ArtefactIncomingLinkView"))
    outgoing = " ".join(_tab_cells(target_page, "ArtefactOutgoingLinkView"))
    assert "Artefact A" in incoming and "Artefact C" not in incoming
    assert "Artefact C" in outgoing and "Artefact A" not in outgoing


def test_incoming_tab_labels_the_other_end_and_the_suspect_flag(client, app, chain):
    page = _body(client, f"/artefactview/show/{chain['B']}")
    cells = _tab_cells(page, "ArtefactIncomingLinkView")
    assert "Chains to" in cells
    assert "Suspect - re-check" in cells  # the AB link was made suspect
    assert "Traced from" in page


def test_incoming_view_is_pinned_to_the_target_end():
    """F.A.B. would otherwise hand both tabs the same (first) relation."""
    outgoing = ArtefactOutgoingLinkView()
    incoming = ArtefactIncomingLinkView()
    assert outgoing.datamodel.get_related_fk(Artefact) == "source"
    assert incoming.datamodel.get_related_fk(Artefact) == "target"
    assert incoming.list_title and incoming.list_title != outgoing.list_title


def test_incoming_tab_is_read_only(client, app, chain):
    for route in ("add", "edit/1", "delete/1"):
        assert client.get(f"/artefactincominglinkview/{route}").status_code == 404
    assert client.get("/artefactincominglinkview/list/").status_code == 200


# ---------------------------------------------------------------------------
# C24 — add a trace link from the artefact you are looking at
# ---------------------------------------------------------------------------
def test_add_link_from_here_pins_this_artefact(client, app, chain):
    resp = client.post(f"/artefactview/action/trace_from/{chain['A']}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/relationshipview/add?_flt_0_source={chain['A']}")

    form = _body(client, resp.headers["Location"])
    # the pinned end is not a choice any more: only the two real decisions remain
    assert re.findall(r'<label[^>]*for="([^"]+)"', form) == ["relationship_type", "target"]
    assert f'value="{chain["A"]}"' in form


def test_pinned_link_takes_its_project_from_the_pinned_end(client, app, chain):
    """The pinned end is enough to scope the link: only type and far end are asked."""
    with app.app_context():
        login_as(app)
        existing = db.session.query(ArtefactRelationship).count()
        foreign = add_artefact(chain["other"], title="Foreign one")
        create_version(foreign, "Initial draft")
        foreign_id = foreign.id

    # A -> D does not exist yet and both ends are in project TRC
    client.post(
        f"/relationshipview/add?_flt_0_source={chain['A']}",
        data={"relationship_type": str(chain["rel_type"]), "target": str(chain["D"])},
        follow_redirects=True,
    )
    with app.app_context():
        created = (
            db.session.query(ArtefactRelationship)
            .filter_by(source_id=chain["A"], target_id=chain["D"])
            .one()
        )
        assert created.project_id == chain["project"]
        assert db.session.query(ArtefactRelationship).count() == existing + 1

    # other project: refused with the rule spelled out, and nothing created
    refused = client.post(
        f"/relationshipview/add?_flt_0_source={chain['A']}",
        data={"relationship_type": str(chain["rel_type"]), "target": str(foreign_id)},
        follow_redirects=True,
    )
    assert "same project" in re.sub(r"\s+", " ", refused.data.decode("utf-8", "replace"))
    with app.app_context():
        assert db.session.query(ArtefactRelationship).count() == existing + 1


# ---------------------------------------------------------------------------
# C21 — the reports lead somewhere, and depth is a control
# ---------------------------------------------------------------------------
def test_impact_rows_link_to_the_artefacts_they_name(client, app, chain):
    html = _body(client, f"/impactview/analyse/{chain['A']}/?depth=2")
    for label in ("B", "C"):
        assert f'href="/artefactview/show/{chain[label]}"' in html, label
    assert f'href="/tracegraphview/show/{chain["B"]}/"' in html
    # each row can be re-analysed from where it is
    assert f'/impactview/analyse/{chain["B"]}/?depth=2' in html


def test_impact_depth_is_a_control_and_is_clamped(client, app, chain):
    counts = {}
    for depth in (1, 2, 3):
        html = _body(client, f"/impactview/analyse/{chain['A']}/?depth={depth}")
        counts[depth] = html.count('href="/artefactview/show/')
        assert f'Hops' in html
    # A->B->C->D: 1 hop reaches B, 2 hops reach B and C, 3 hops reach all three
    assert counts[1] < counts[2] < counts[3]

    clamped = _body(client, f"/impactview/analyse/{chain['A']}/?depth=99")
    assert "depth=5" in clamped  # capped, not run away
    garbage = _body(client, f"/impactview/analyse/{chain['A']}/?depth=nonsense")
    assert "depth=1" in garbage  # falls back to the default


def test_suspect_links_lead_to_the_link_itself(client, app, chain):
    html = _body(client, f"/impactview/analyse/{chain['B']}/")
    assert f'href="/relationshipview/show/{chain["link_ab"]}"' in html
    assert "open the link to mark it reviewed" in html


def test_graph_and_impact_pages_point_at_each_other(client, app, chain):
    graph = _body(client, f"/tracegraphview/show/{chain['A']}/")
    impact = _body(client, f"/impactview/analyse/{chain['A']}/")
    assert f'href="/impactview/analyse/{chain["A"]}/"' in graph
    assert f'href="/tracegraphview/show/{chain["A"]}/"' in impact
    assert f'href="/artefactview/show/{chain["A"]}"' in graph
    assert f'href="/artefactview/edit/{chain["A"]}"' in impact


def test_graph_nodes_lead_to_their_artefact_pages(client, app, chain):
    html = _body(client, f"/tracegraphview/show/{chain['A']}/")
    assert "nodeUrls" in html
    assert f'"/artefactview/show/{chain["B"]}"' in html
    assert "doubleClick" in html


def test_orphan_report_links_each_candidate_with_a_way_out(client, app, chain):
    with app.app_context():
        login_as(app)
        project = add_project("ORP", "Orphan project").id
        lonely = add_artefact(project, title="Lonely requirement")
        lonely_id = lonely.id
    html = _body(client, "/impactview/orphans/")
    assert f'href="/artefactview/show/{lonely_id}"' in html
    assert f'/relationshipview/add?_flt_0_source={lonely_id}' in html
    assert f"/impactview/analyse/{lonely_id}" in html
