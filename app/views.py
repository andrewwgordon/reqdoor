import json

from flask import current_app, flash, g, redirect, request, url_for
from flask_appbuilder import (
    BaseView,
    IndexView,
    ModelView,
    action,
    expose,
    has_access,
)
from flask_appbuilder.charts.views import GroupByChartView
from flask_appbuilder.models.group import aggregate_count
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_babel import lazy_gettext as _
from sqlalchemy import func
from wtforms import StringField

from .extensions import db
from .security import prune_stale_permissions
from .models import (
    Approval,
    Artefact,
    ArtefactRelationship,
    ArtefactVersion,
    Baseline,
    BaselineEntry,
    ChangeRequest,
    ChangeSetItem,
    ChangeState,
    Comment,
    Decision,
    Project,
    RelationshipType,
    RequirementState,
    Review,
    ReviewAssignment,
    ReviewState,
    ReviewVerdict,
)
from .services import (
    REVIEW_DECIDABLE_FROM,
    baseline_skipped,
    baselines_holding,
    can_transition,
    clear_suspect,
    create_review_from_baseline,
    create_version,
    default_baseline_name,
    differs_from_version,
    graph_data,
    impact_report,
    latest_version,
    next_change_state,
    next_review_state,
    orphan_artefacts,
    record_approval,
    reconcile_artefact,
    request_changes_state,
    restore_version,
    review_artefact_version,
    snapshot_baseline,
    trace_links_holding,
    validate_relationship,
    validate_verdict_scope,
)

# ---------------------------------------------------------------------------
# Read-only reusable permissions
# ---------------------------------------------------------------------------
READ_ONLY = ["can_list", "can_show"]

OPEN_REVIEW_STATES = (ReviewState.PLANNED, ReviewState.ACTIVE, ReviewState.COMMENT_RESOLUTION)

# Menu categories, in the order of the governance loop they support:
# capture -> baseline/review/change -> analyse -> overview -> configure.
CAT_REQUIREMENTS = "Requirements"
CAT_GOVERNANCE = "Governance"
CAT_ANALYSIS = "Analysis"
CAT_DASHBOARD = "Dashboard"
CAT_ADMINISTRATION = "Administration"

# One-click saved filters (milestone C9). These are plain F.A.B. list URLs, so the
# result set can be searched/ordered further and linked to from anywhere.
FILTER_DRAFT_ARTEFACTS = "/artefactview/list/?_flt_0_status=DRAFT"
FILTER_APPROVED_ARTEFACTS = "/artefactview/list/?_flt_0_status=APPROVED"
FILTER_SUSPECT_LINKS = "/relationshipview/list/?_flt_0_is_suspect=1"
FILTER_ACTIVE_REVIEWS = "/reviewview/list/?_flt_0_status=ACTIVE"
FILTER_OPEN_CHANGES = "/changerequestview/list/?_flt_0_status=SUBMITTED"
FILTER_ORPHANS = "/impactview/orphans/"

# ---------------------------------------------------------------------------
# Reader-first field groups, labels and hints (C13)
# ---------------------------------------------------------------------------
# Where a view declares the matching fieldset, F.A.B. derives the *_columns list
# from it, so the fieldset is the single source of truth for what the screen
# shows - a field that is not grouped is not on the screen.
AUDIT_FIELDS = ["created_by", "created_on", "changed_by", "changed_on"]

AUDIT_LABELS = {
    "created_on": _("Created"),
    "created_by": _("Created by"),
    "changed_on": _("Last changed"),
    "changed_by": _("Last changed by"),
}

VERSIONING_HINT = _(
    "Saving an artefact records an immutable new version - history is never "
    "overwritten."
)
LIFECYCLE_HINT = _(
    "New artefacts start as Draft. Use the Approve action on the artefact page to "
    "move it to Approved (that also records a version and makes it baselinable)."
)
BASELINE_NAME_HINT = _(
    "Only artefacts whose latest version is Approved are captured. Leave the name "
    "blank to date-stamp it as '<project code> BL <date>'."
)

# ---------------------------------------------------------------------------
# Readable cell values (C13/C15)
# ---------------------------------------------------------------------------
# F.A.B. prints a formatter's *cell value*, so these take the value and return
# text. Without them the screens show ``None``, ``True``/``False`` and timestamps
# with microseconds.
DATE_FORMAT = "%d %b %Y"
TIMESTAMP_FORMAT = "%d %b %Y %H:%M"
DASH = "\u2014"


def _date(value):
    return value.strftime(DATE_FORMAT) if value else DASH


def _timestamp(value):
    return value.strftime(TIMESTAMP_FORMAT) if value else DASH


def _yes_no(value):
    return _("Yes") if value else _("No")


def _suspect(value):
    return _("Suspect - re-check") if value else _("Checked")


def _resolved(value):
    return _("Resolved") if value else _("Open")


def _applied(value):
    return _("Applied") if value else _("Not applied yet")


def _or_dash(value):
    return value if value not in (None, "") else DASH


AUDIT_FORMATTERS = {"created_on": _timestamp, "changed_on": _timestamp}
SUSPECT_FORMATTERS = {"is_suspect": _suspect}


PICKER_LIMIT = 200
DEFAULT_IMPACT_DEPTH = 3
MAX_IMPACT_DEPTH = 5

# Row-based views page at no more than 25 rows. F.A.B.'s default is already 25;
# the constant is declared so the cap is explicit on every view and the test
# suite can enforce it if the framework default ever changes.
PAGE_SIZE = 25


def analysis_picker(request, project_id=None):
    """Picker data for the trace/impact entry pages (C22).

    Never loads every artefact: the artefact list only exists once a project is
    chosen, and it is capped so one big project cannot swamp the page.
    """
    chosen = project_id or request.args.get("project", type=int)
    artefacts, truncated = [], False
    if chosen:
        found = (
            db.session.query(Artefact)
            .filter_by(project_id=chosen)
            .order_by(Artefact.kind, Artefact.title)
            .limit(PICKER_LIMIT + 1)
            .all()
        )
        truncated = len(found) > PICKER_LIMIT
        artefacts = found[:PICKER_LIMIT]
    return {
        "projects": db.session.query(Project).order_by(Project.project_code).all(),
        "project_id": chosen,
        "artefacts": artefacts,
        "truncated": truncated,
        "picker_limit": PICKER_LIMIT,
    }


def impact_depth(request):
    """Requested traversal depth, clamped to something the page can draw (C21b)."""
    depth = request.args.get("depth", type=int) or DEFAULT_IMPACT_DEPTH
    return max(1, min(depth, MAX_IMPACT_DEPTH))


def show_redirect(view, pk, view_name=None):
    """Redirect back to a record's own detail page.

    Every row ``@action`` used to end in ``redirect(self.get_redirect())``, but
    F.A.B. only stores the last *list/add/edit* URL (``show`` never calls
    ``update_redirect()``), so acting on a record bounced the user to a stale
    page - usually the empty index - and lost their place in the workflow.

    :param view: the view instance the action ran on (source of the endpoint name)
    :param pk: primary key of the record to open
    :param view_name: override the target view (e.g. an action on a version that
        should land the user back on the parent artefact)
    """
    endpoint = f"{view_name or type(view).__name__}.show"
    return redirect(url_for(endpoint, pk=pk))


def flash_baseline_result(baseline, count):
    """One wording for both baseline doors (the project action and Baselines>Add).

    Returns True when the baseline holds something. When it does not, the caller
    must throw the empty baseline away: an immutable baseline with no content is
    a trap for whoever opens it next.
    """
    if not count:
        flash(
            _("Nothing to baseline in '%(name)s' yet - only Approved artefacts are "
              "captured, and this project has none.", name=baseline.name),
            "warning",
        )
        return False
    flash(
        _("Baseline '%(name)s' captured %(count)s approved artefact version(s).",
          name=baseline.name, count=count),
        "success",
    )
    skipped = baseline_skipped(baseline)
    if skipped:
        names = ", ".join(artefact.title for artefact in skipped[:5])
        if len(skipped) > 5:
            names += _(", …")
        flash(
            _("%(count)s artefact(s) were skipped because their latest version is "
              "not Approved: %(names)s", count=len(skipped), names=names),
            "info",
        )
    return True


class SingleRecordActionMixin:
    """Row actions here act on one record at a time.

    Every ``@action`` in this app is written as ``def action(self, item)`` and is
    declared ``multiple=False``, so F.A.B. only offers it on a record page. This
    mixin also refuses a hand-crafted bulk POST: without it F.A.B. would call the
    handler with a *list* of records and raise a 500.
    """

    @expose("/action_post", methods=["POST"])
    def action_post(self):
        """Refuse a bulk dispatch politely (see the class docstring).

        Deliberately *not* decorated with ``@has_access``: F.A.B. checks the
        per-action permission inside its own handlers, and ``can_action_post`` is
        not part of a restricted view's ``base_permissions``, so decorating this
        would turn a friendly warning into a bare 403.
        """
        flash(
            _("This action applies to one record at a time. Open the record and "
              "use the action there."),
            "warning",
        )
        return redirect(self.get_redirect())

    @expose("/action/<string:name>/<pk>", methods=["GET", "POST"])
    def action(self, name, pk):
        """Refuse an unknown action name politely.

        Renaming or replacing a button (Milestone 3 did just that) leaves old
        bookmarks and half-clicked links pointing at an action that no longer
        exists; F.A.B. would crash on it with an AttributeError. Known names fall
        through to F.A.B., which enforces the per-action permission itself.
        """
        if name not in self.actions:
            flash(
                _("'%(name)s' is not an action on this screen any more. Open the "
                  "record and choose one of the buttons shown there.", name=name),
                "warning",
            )
            return redirect(url_for(f"{type(self).__name__}.list"))
        return super().action(name, pk)


class ProjectView(SingleRecordActionMixin, ModelView):
    """The one and only project screen: CRUD plus a project workspace show page
    (tabs for the project's artefacts, baselines, reviews and change requests) and
    a row action that baselines the project's current approved artefact versions."""

    datamodel = SQLAInterface(Project)
    page_size = PAGE_SIZE
    list_title = _("Projects")
    show_title = _("Project")
    add_title = _("Add project")
    edit_title = _("Edit project")
    list_columns = ["project_code", "name"]
    show_columns = ["project_code", "name", "description"]
    search_columns = ["project_code", "name"]
    add_fieldsets = [
        (_("Project details"), {"fields": ["project_code", "name", "description"]})
    ]
    edit_fieldsets = [
        (_("Project details"), {"fields": ["project_code", "name", "description"]})
    ]
    show_fieldsets = [
        (_("Project details"), {"fields": ["project_code", "name", "description"]}),
        (_("Record"), {"fields": AUDIT_FIELDS, "expanded": False}),
    ]
    label_columns = {
        "project_code": _("Code"),
        "name": _("Project name"),
        **AUDIT_LABELS,
    }
    formatters_columns = {**AUDIT_FORMATTERS, "description": _or_dash}
    description_columns = {
        "project_code": _("Short unique code - it also names the project's baselines."),
        "description": _("What the project covers (optional)."),
    }
    base_order = ("project_code", "asc")

    @action(
        "create_baseline",
        _("Create Baseline from project"),
        _("Create a baseline snapshotting this project's current approved "
          "artefact versions?"),
        "fa-lock",
        multiple=False,
    )
    def create_baseline(self, item):
        """Baseline the project's currently-approved artefact versions (FR-P4)."""
        name = default_baseline_name(item)
        baseline = Baseline(
            project_id=item.id,
            name=name,
            description=str(_("Baseline created from the project page.")),
        )
        db.session.add(baseline)
        db.session.commit()
        count = snapshot_baseline(baseline)
        current_app.logger.info(
            "baseline %s created with %s approved artefact version(s)",
            baseline.id,
            count,
        )
        if not flash_baseline_result(baseline, count):
            db.session.delete(baseline)
            db.session.commit()
        return show_redirect(self, item.id)


# ---------------------------------------------------------------------------
# Artefacts + immutable versions (Phase 1)
# ---------------------------------------------------------------------------
class ArtefactVersionView(SingleRecordActionMixin, ModelView):
    """Immutable version history. Read-only with a Restore action."""

    datamodel = SQLAInterface(ArtefactVersion)
    page_size = PAGE_SIZE
    list_title = _("Versions")
    show_title = _("Version")
    base_permissions = READ_ONLY + ["can_restore"]
    base_order = ("version_number", "desc")
    exclude_route_methods = {"add", "edit", "delete", "download"}
    search_columns = ["title", "kind", "status", "change_summary"]
    list_columns = [
        "version_number",
        "title",
        "status",
        "priority",
        "change_summary",
        "created_by",
        "created_on",
    ]
    show_fieldsets = [
        (_("Content as it stood"), {"fields": ["kind", "title", "content"]}),
        (_("Lifecycle"), {"fields": ["status", "priority"]}),
        (_("Record"), {"fields": ["version_number", "change_summary"] + AUDIT_FIELDS,
                       "expanded": False}),
    ]
    label_columns = {
        "version_number": _("Version"),
        "change_summary": _("What changed"),
        "created_on": _("Version created"),
        **AUDIT_LABELS,
    }
    formatters_columns = {**AUDIT_FORMATTERS, "content": _or_dash}

    @action(
        "restore",
        _("Restore artefact to this version"),
        _("The artefact will move to the content of this version and a new "
          "version will be recorded. Continue?"),
        "fa-undo",
        multiple=False,
    )
    def restore(self, item):
        artefact_id = item.artefact_id
        restore_version(item.artefact, item)
        flash(
            _("Artefact restored to version %(v)s; a new version was recorded.",
              v=item.version_number),
            "success",
        )
        # Land on the artefact: its Versions tab is where this action lives.
        return show_redirect(self, artefact_id, "ArtefactView")


class ArtefactView(SingleRecordActionMixin, ModelView):
    datamodel = SQLAInterface(Artefact)
    related_views = [ArtefactVersionView]
    list_title = _("Artefacts")
    show_title = _("Artefact")
    add_title = _("Add artefact (starts as Draft)")
    edit_title = _("Edit artefact")
    page_size = PAGE_SIZE
    # Group an artefact's list with its own project instead of interleaving every
    # project alphabetically, and show the reference the rest of the UI quotes.
    base_order = ("project_id", "asc")
    list_columns = [
        "id",
        "project",
        "kind",
        "title",
        "status",
        "priority",
        "changed_on",
    ]
    search_columns = ["project", "kind", "title", "status", "priority"]
    # The add form deliberately has no 'status': a new artefact is a Draft, and
    # approval is a deliberate action on the artefact page (FR-P4 gate).
    add_fieldsets = [
        (_("Artefact"), {
            "fields": ["project", "kind", "title", "content", "priority"]
        })
    ]
    edit_fieldsets = [
        (_("Artefact"), {"fields": ["kind", "title", "content"]}),
        (_("Lifecycle"), {"fields": ["status", "priority"]}),
    ]
    show_fieldsets = [
        (_("Artefact"), {"fields": ["project", "kind", "title", "content"]}),
        (_("Lifecycle"), {"fields": ["status", "priority"]}),
        (_("Record"), {"fields": AUDIT_FIELDS, "expanded": False}),
    ]
    label_columns = {
        "id": _("Ref"),
        "kind": _("Type"),
        "status": _("Lifecycle"),
        "priority": _("Priority"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "content": VERSIONING_HINT,
        "title": _("A short, unique statement of what is needed."),
        "status": LIFECYCLE_HINT,
        "priority": _("How important it is, not how urgent the work is."),
        "project": _("The project an artefact belongs to cannot be changed later - "
                     "trace links and baselines are scoped to it."),
    }
    formatters_columns = {**AUDIT_FORMATTERS, "content": _or_dash}

    @action(
        "approve",
        _("Approve artefact"),
        _("Set this artefact's status to Approved and record an immutable "
          "version (it then becomes baselinable)?"),
        "fa-check-circle",
        multiple=False,
    )
    def approve(self, item):
        """Quick status → Approved, versioned, so it can be baselined."""
        if item.status == RequirementState.APPROVED:
            flash(_("Artefact is already Approved."), "warning")
        else:
            item.status = RequirementState.APPROVED
            db.session.commit()
            create_version(item, "Status set to Approved")
            flash(_("Artefact approved and versioned — it can now be baselined."),
                  "success")
        return show_redirect(self, item.id)

    def post_add(self, item):
        create_version(item, "Initial draft")

    def post_update(self, item):
        # Version only real changes: the live row mirrors its latest version, so
        # an unchanged Save must not inflate the immutable history (FR-P3/FR-P4).
        if differs_from_version(item, latest_version(item)):
            create_version(item, "Edited")

    def _show(self, pk):
        # Display invariant (FR-P3): an artefact always shows the properties of
        # its latest version, so re-align the live row before rendering. This is
        # a no-op in normal flows (writes keep the row mirrored) and only
        # corrects drift from writes that bypassed versioning.
        item = self.datamodel.get(pk, self._base_filters)
        if item is not None:
            reconcile_artefact(item)
        return super()._show(pk)

    @action(
        "obsolete",
        _("Mark obsolete"),
        _("Retire this artefact: it stays in history and in any baseline, but stops "
          "being worked on?"),
        "fa-ban",
        multiple=False,
    )
    def obsolete(self, item):
        """The door that replaces deletion for baselined/trace-linked artefacts."""
        if item.status == RequirementState.OBSOLETE:
            flash(_("Artefact is already Obsolete."), "warning")
        else:
            item.status = RequirementState.OBSOLETE
            db.session.commit()
            create_version(item, "Marked obsolete")
            flash(_("Artefact marked Obsolete and versioned - it is retired from "
                    "the work but kept as evidence."), "success")
        return show_redirect(self, item.id)

    def pre_delete(self, item):
        baselines = [baseline.name for baseline in baselines_holding(item)]
        links = trace_links_holding(item)
        if not (baselines or links):
            return
        reasons = []
        if baselines:
            reasons.append(
                str(_("it is frozen in the baseline(s) %(names)s",
                      names=", ".join(baselines[:3])))
            )
        if links:
            reasons.append(str(_("it has %(count)s trace link(s)", count=links)))
        raise Exception(
            "This artefact cannot be deleted because " + " and ".join(reasons) +
            ". Use 'Mark obsolete' to retire it instead - that keeps the evidence "
            "the baseline and the trace links depend on."
        )

    @action(
        "trace",
        _("Open traceability graph"),
        _("Open the traceability graph centred on this artefact?"),
        "fa-share-alt",
        multiple=False,
    )
    def trace(self, item):
        return redirect(url_for("TraceGraphView.show", pk=item.id))

    @action(
        "impact",
        _("Impact report"),
        _("What depends on this artefact, and what it depends on?"),
        "fa-sitemap",
        multiple=False,
    )
    def impact(self, item):
        return redirect(url_for("ImpactAnalysisView.analyse", pk=item.id))

    @action(
        "trace_from",
        _("Add trace link from here"),
        None,
        "fa-long-arrow-right",
        multiple=False,
    )
    def trace_from(self, item):
        """Start a link with this artefact already in the 'from' end (C24).

        F.A.B. hides a relation the URL already pins, so the form is left with the
        two choices that actually matter: the link type and the other end.
        """
        return redirect(
            url_for("RelationshipView.add", **{"_flt_0_source": item.id})
        )


# ---------------------------------------------------------------------------
# Phase 2 — Traceability (FR-5)
# ---------------------------------------------------------------------------
class RelationshipTypeView(ModelView):
    datamodel = SQLAInterface(RelationshipType)
    page_size = PAGE_SIZE
    list_title = _("Link types")
    show_title = _("Link type")
    add_title = _("Add link type")
    edit_title = _("Edit link type")
    search_columns = ["display_name", "name", "is_directional"]
    list_columns = ["display_name", "name", "is_directional"]
    edit_fieldsets = [
        (_("Link type"), {
            "fields": ["display_name", "name", "description", "is_directional"]
        }),
        (_("Kind rules (optional)"), {"fields": ["source_kinds", "target_kinds"]}),
    ]
    add_fieldsets = edit_fieldsets
    show_fieldsets = edit_fieldsets
    label_columns = {
        "display_name": _("Shown as"),
        "name": _("Internal name"),
        "is_directional": _("Directional"),
        "source_kinds": _("Allowed at the start"),
        "target_kinds": _("Allowed at the end"),
    }
    formatters_columns = {"is_directional": lambda value: _("One way") if value else _("Either way"), "description": _or_dash}
    description_columns = {
        "display_name": _("Label used on links and in the graph, e.g. 'Verifies'."),
        "name": _("Stable identifier used by reports and imports, e.g. 'VERIFIES'."),
        "is_directional": _("Leave ticked so the link only reads start \u2192 end; untick to allow it either way."),
        "source_kinds": _(
            "JSON list of artefact types allowed at the start, e.g. "
            '["SOFTWARE_REQ"]. Empty means any type.'
        ),
        "target_kinds": _(
            "JSON list of artefact types allowed at the end, e.g. "
            '["TEST_CASE"]. Empty means any type.'
        ),
    }


class ArtefactOutgoingLinkView(ModelView):
    """Read-only outgoing trace links, shown as a tab on the artefact detail page."""

    datamodel = SQLAInterface(ArtefactRelationship)
    page_size = PAGE_SIZE
    list_title = _("Traces (out)")
    show_title = _("Trace link")
    base_permissions = READ_ONLY
    exclude_route_methods = {"add", "edit", "delete", "download"}
    search_columns = ["relationship_type", "target", "is_suspect"]
    list_columns = ["relationship_type", "target", "is_suspect", "changed_on"]
    show_columns = ["project", "relationship_type", "target", "is_suspect", "created_on"]
    label_columns = {
        "relationship_type": _("Link type"),
        "target": _("Traces to"),
        "is_suspect": _("Suspect"),
        **AUDIT_LABELS,
    }
    formatters_columns = {**AUDIT_FORMATTERS, **SUSPECT_FORMATTERS}


class _TargetSideInterface(SQLAInterface):
    """Pins the parent link F.A.B. uses for a tab to the *target* end.

    ``SQLAInterface.get_related_fk()`` returns the first relationship back to
    ``Artefact``, which is always ``source`` - so a second trace tab would show
    the same outgoing rows as the first one.
    """

    def get_related_fk(self, model):
        return "target" if model is Artefact else super().get_related_fk(model)


class ArtefactIncomingLinkView(ModelView):
    """Read-only incoming trace links, shown as a tab on the artefact detail page.

    Traceability is only useful if you can see both ends of the arrow from the
    artefact you are looking at (C23).
    """

    datamodel = _TargetSideInterface(ArtefactRelationship)
    page_size = PAGE_SIZE
    list_title = _("Traces (in)")
    show_title = _("Trace link")
    base_permissions = READ_ONLY
    exclude_route_methods = {"add", "edit", "delete", "download"}
    search_columns = ["relationship_type", "source", "is_suspect"]
    list_columns = ["relationship_type", "source", "is_suspect", "changed_on"]
    show_columns = ["project", "source", "relationship_type", "target", "is_suspect", "created_on"]
    label_columns = {
        "relationship_type": _("Link type"),
        "source": _("Traced from"),
        "is_suspect": _("Suspect"),
        **AUDIT_LABELS,
    }
    formatters_columns = {**AUDIT_FORMATTERS, **SUSPECT_FORMATTERS}


class RelationshipView(SingleRecordActionMixin, ModelView):
    datamodel = SQLAInterface(ArtefactRelationship)
    page_size = PAGE_SIZE
    list_title = _("Trace links")
    show_title = _("Trace link")
    add_title = _("Add trace link")
    edit_title = _("Edit trace link")
    base_order = ("project_id", "asc")
    list_columns = ["source", "relationship_type", "target", "is_suspect"]
    search_columns = ["project", "source", "target", "relationship_type", "is_suspect"]
    add_fieldsets = [
        (_("Trace link"), {"fields": ["source", "relationship_type", "target"]})
    ]
    show_fieldsets = [
        (_("Trace link"), {
            "fields": ["project", "source", "relationship_type", "target", "is_suspect"]
        }),
        (_("Record"), {"fields": ["created_by", "created_on"], "expanded": False}),
    ]
    label_columns = {
        "source": _("From artefact"),
        "target": _("To artefact"),
        "relationship_type": _("Link type"),
        "is_suspect": _("Suspect"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "source": _("Where the arrow starts, e.g. the test case."),
        "target": _("Where the arrow points, e.g. the requirement it verifies."),
        "is_suspect": _(
            "Set automatically when the source artefact changes; clear it with "
            "'Mark as reviewed' once the link has been re-checked."
        ),
    }
    formatters_columns = {**AUDIT_FORMATTERS, **SUSPECT_FORMATTERS}

    def pre_add(self, item):
        try:
            validate_relationship(item.source, item.target, item.relationship_type)
        except ValueError as exc:
            raise Exception(str(exc))
        item.project_id = item.source.project_id

    @action(
        "markvalid",
        _("Mark as reviewed"),
        _("Clear the suspect flag on this link?"),
        "fa-check",
        multiple=False,
    )
    def mark_valid(self, item):
        clear_suspect(item)
        flash(_("Suspect flag cleared."), "success")
        return show_redirect(self, item.id)


class TraceGraphView(BaseView):
    """Interactive traceability graph (vis.js) centred on an artefact."""

    route_base = "/tracegraphview"
    default_view = "index"

    @expose("/")
    @has_access
    def index(self):
        # The picker form posts a plain GET to this URL (``?pk=<id>``), so honour
        # it here instead of re-rendering the picker and doing nothing.
        pk = request.args.get("pk", type=int)
        if pk:
            return redirect(url_for("TraceGraphView.show", pk=pk))
        self.update_redirect()
        return self.render_template(
            "trace_graph_start.html",
            action_url=url_for("TraceGraphView.index"),
            submit_label=_("Show graph"),
            help_text=_(
                "The graph shows the chosen artefact's network out to two hops, with "
                "suspect links drawn dashed."
            ),
            **analysis_picker(request),
        )

    @expose("/show/<int:pk>/")
    @has_access
    def show(self, pk):
        root = db.session.get(Artefact, pk)
        if root is None:
            flash(_("Artefact not found."), "danger")
            return redirect(url_for("TraceGraphView.index"))
        self.update_redirect()
        nodes, edges = graph_data(root.id)
        # node id -> artefact page, so a double-click in the graph goes somewhere
        node_urls = {
            str(node["id"]): url_for("ArtefactView.show", pk=node["id"]) for node in nodes
        }
        return self.render_template(
            "trace_graph.html",
            root=root,
            nodes_json=json.dumps(nodes),
            edges_json=json.dumps(edges),
            node_urls_json=json.dumps(node_urls),
            # the picker defaults to the artefact you came from (C22)
            action_url=url_for("TraceGraphView.index"),
            submit_label=_("Show graph"),
            **analysis_picker(request, project_id=root.project_id),
        )


# ---------------------------------------------------------------------------
# Dashboard — quickcharts layout (GroupByChartView)
# ---------------------------------------------------------------------------
def _kind_label(value):
    """Chart formatter: enum kind → readable value."""
    return value.value if value is not None else ""


def _status_label(value):
    """Chart formatter: enum status → readable value."""
    return value.value if value is not None else ""


def _project_label(project_id):
    """Chart formatter: project id → project code."""
    if project_id is None:
        return ""
    project = db.session.get(Project, project_id)
    return project.project_code if project is not None else str(project_id)


class ArtefactDistributionChartView(GroupByChartView):
    """Pie distribution of artefacts by kind and by status."""

    datamodel = SQLAInterface(Artefact)
    chart_title = _("Artefacts by kind & status")
    # An empty list is ignored by F.A.B. (it falls back to every column), so give
    # the dashboard a short, meaningful scoping set instead of 15 junk filters.
    search_columns = ["project", "kind", "status"]
    chart_type = "PieChart"
    chart_3d = "false"
    height = "440px"
    definitions = [
        {"label": _("By kind"), "group": "kind", "formatter": _kind_label,
         "series": [(aggregate_count, "id")]},
        {"label": _("By status"), "group": "status", "formatter": _status_label,
         "series": [(aggregate_count, "id")]},
    ]


class ArtefactPerProjectChartView(GroupByChartView):
    """Column chart of artefacts per project."""

    datamodel = SQLAInterface(Artefact)
    chart_title = _("Artefacts per project")
    search_columns = ["project"]
    chart_type = "ColumnChart"
    chart_3d = "false"
    height = "440px"
    # Group by the real ``project_id`` column (orderable at the DB level) and
    # format the label to the project code afterwards.
    definitions = [
        {"label": _("Project"), "group": "project_id", "formatter": _project_label,
         "series": [(aggregate_count, "id")]},
    ]


class ImpactAnalysisView(BaseView):
    """Forward/reverse impact analysis, suspect-link list and orphan report."""

    route_base = "/impactview"
    default_view = "index"

    @expose("/")
    @has_access
    def index(self):
        # The picker form posts a plain GET to this URL (``?pk=<id>``); analyse it
        # here rather than re-rendering the picker and doing nothing.
        pk = request.args.get("pk", type=int)
        if pk:
            return redirect(url_for("ImpactAnalysisView.analyse", pk=pk))
        self.update_redirect()
        return self.render_template(
            "impact_index.html",
            action_url=url_for("ImpactAnalysisView.index"),
            submit_label=_("Analyse impact"),
            help_text=_(
                "Impacted artefacts are found by following trace links forward "
                "(what this affects) and back (what depends on it). Suspect links "
                "in that set are listed separately."
            ),
            orphans_url=url_for("ImpactAnalysisView.orphans"),
            **analysis_picker(request),
        )

    @expose("/analyse/<int:pk>/")
    @has_access
    def analyse(self, pk):
        root = db.session.get(Artefact, pk)
        if root is None:
            flash(_("Artefact not found."), "danger")
            return redirect(url_for("ImpactAnalysisView.index"))
        self.update_redirect()
        depth = impact_depth(request)
        report = impact_report(root.id, max_depth=depth)
        return self.render_template(
            "impact.html",
            root=root,
            depth=depth,
            max_depth=MAX_IMPACT_DEPTH,
            impacted=report["impacted"],
            suspects=report["suspects"],
            # the picker defaults to the artefact you came from (C22)
            action_url=url_for("ImpactAnalysisView.index"),
            submit_label=_("Analyse impact"),
            orphans_url=url_for("ImpactAnalysisView.orphans"),
            **analysis_picker(request, project_id=root.project_id),
        )

    @expose("/orphans/")
    @has_access
    def orphans(self):
        self.update_redirect()
        projects = db.session.query(Project).order_by(Project.name).all()
        grouped = {p: orphan_artefacts(p.id) for p in projects}
        return self.render_template("impact_orphans.html", grouped=grouped)


# ---------------------------------------------------------------------------
# Phase 2 — Reviews (FR-7)
# ---------------------------------------------------------------------------
class ReviewView(SingleRecordActionMixin, ModelView):
    datamodel = SQLAInterface(Review)
    page_size = PAGE_SIZE
    related_views = []
    list_title = _("Reviews")
    show_title = _("Review")
    add_title = _("Add review (starts as Planned)")
    edit_title = _("Edit review")
    base_order = ("due_date", "asc")
    # Explicit order list: `progress` is a model property, not a column, and must
    # never be used as an ORDER BY (would break the query).
    order_columns = ["title", "project", "status", "due_date", "created_by", "created_on"]
    list_columns = ["title", "project", "status", "progress", "due_date", "created_by"]
    search_columns = ["title", "project", "status", "due_date", "created_by"]
    # No 'status' on either form: a review starts as Planned and every state is
    # reached with its actions (Next step, Send back for comment) - C16.
    add_fieldsets = [
        (_("Review"), {
            "fields": ["project", "title", "description", "basis_baseline", "due_date"]
        })
    ]
    edit_fieldsets = [
        (_("Review"), {
            "fields": ["title", "description", "basis_baseline", "due_date"]
        }),
    ]
    show_fieldsets = [
        (_("Review"), {
            "fields": [
                "title", "project", "status", "progress", "due_date", "basis_baseline",
                "description",
            ]
        }),
        (_("Record"), {"fields": ["created_by", "created_on"], "expanded": False}),
    ]
    label_columns = {
        "basis_baseline": _("Review basis (baseline)"),
        "progress": _("Decision progress"),
        "due_date": _("Due by"),
        "status": _("State"),
        "description": _("Purpose"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "basis_baseline": _(
            "Leave empty to review the live project content. Pick a baseline to "
            "freeze the review: verdicts and comments then tie to the reviewed "
            "versions, not to later edits."
        ),
        "due_date": _("When every reviewer should have recorded their verdicts."),
        "description": _("What the review is looking for (optional)."),
    }
    formatters_columns = {
        **AUDIT_FORMATTERS,
        "due_date": _date,
        # 'None'/'empty' read as a decision, not as a missing value.
        "basis_baseline": lambda value: value or _("Live project content"),
        "progress": lambda value: value or _("Not started - no reviewers yet"),
        "description": _or_dash,
    }

    def _transition(self, item, target):
        """Guarded state change + a flash, always returning to the review page."""
        if can_transition(item.status, target):
            item.status = target
            db.session.commit()
            flash(_("Review moved to %(state)s.", state=target.value), "success")
        else:
            flash(_("Cannot move review from %(cur)s to %(tgt)s.",
                    cur=item.status.value, tgt=target.value), "danger")
        return show_redirect(self, item.id)

    @action(
        "advance",
        _("Next step"),
        _("Move this review one step along the path "
          "Planned - Active - Approved - Closed?"),
        "fa-arrow-right",
        multiple=False,
    )
    def advance(self, item):
        """One button for the whole review path; the workflow table decides it.

        The old screen offered Start/Approve/Close at the same time, so two of the
        three always failed with a red warning.
        """
        target = next_review_state(item.status)
        if target is None:
            flash(
                _("Nothing left to do - this review is %(state)s.",
                  state=item.status.value),
                "info",
            )
            return show_redirect(self, item.id)
        return self._transition(item, target)

    @action(
        "request_changes",
        _("Send back for comment"),
        _("Ask the authors to resolve the comments before this review can be "
          "approved?"),
        "fa-comment-o",
        multiple=False,
    )
    def request_changes(self, item):
        """The one branch the linear 'Next step' path cannot take."""
        target = request_changes_state(item.status)
        if target is None:
            flash(
                _("Only an Active review can be sent back for comment resolution - "
                  "this one is %(state)s.", state=item.status.value),
                "warning",
            )
            return show_redirect(self, item.id)
        return self._transition(item, target)

    @action(
        "sign_off",
        _("Sign off (approve)"),
        _("Record your approval of this review as an electronic signature "
          "(your user and the time are stored)?"),
        "fa-thumbs-up",
        multiple=False,
    )
    def sign_off(self, item):
        return self._sign(item, Decision.APPROVE)

    @action(
        "sign_dissent",
        _("Sign with dissent"),
        _("Record a dissenting signature against this review (your user and the "
          "time are stored)?"),
        "fa-thumbs-down",
        multiple=False,
    )
    def sign_dissent(self, item):
        return self._sign(item, Decision.REJECT)

    def _sign(self, item, decision):
        """One-click e-signature for the signed-in user (FR-P7).

        Signing is a statement about the review, not a state change: the chair
        still moves it on with 'Next step'.
        """
        if item.status not in REVIEW_DECIDABLE_FROM:
            flash(
                _("Only an Active or Comment Resolution review can be signed - this "
                  "one is %(state)s.", state=item.status.value),
                "warning",
            )
            return show_redirect(self, item.id)
        record_approval(item, g.user, decision)
        flash(
            _("%(user)s signed this review as %(decision)s.",
              user=g.user.username, decision=decision.value),
            "success",
        )
        return show_redirect(self, item.id)


class ReviewVerdictView(ModelView):
    """Per-artefact review verdicts — DNG-style approve / reject / abstain."""

    datamodel = SQLAInterface(ReviewVerdict)
    page_size = PAGE_SIZE
    list_title = _("Verdicts")
    show_title = _("Verdict")
    add_title = _("Record a verdict")
    edit_title = _("Change my verdict")
    search_columns = ["review", "artefact", "reviewer", "decision"]
    list_columns = [
        "reviewer", "artefact", "artefact_version", "decision", "comment", "created_on"
    ]
    show_fieldsets = [
        (_("Verdict"), {"fields": ["decision", "comment"]}),
        (_("What was reviewed"), {
            "fields": ["review", "reviewer", "artefact", "artefact_version"]
        }),
        (_("Record"), {"fields": ["created_on"], "expanded": False}),
    ]
    add_fieldsets = [
        (_("Verdict"), {"fields": ["review", "artefact", "decision", "comment"]})
    ]
    edit_fieldsets = [(_("Verdict"), {"fields": ["decision", "comment"]})]
    label_columns = {
        "artefact_version": _("Reviewed version"),
        "decision": _("Decision"),
        "comment": _("Notes"),
        "artefact": _("Artefact"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "review": _("The review this verdict belongs to."),
        "artefact": _(
            "Pick an artefact in the review's scope: same project, and — for a "
            "baseline review — part of that baseline."
        ),
        "decision": _(
            "Your recommendation. You can change it until the review is approved."
        ),
        "comment": _("Notes for the author (optional, but always appreciated)."),
    }
    formatters_columns = {"created_on": _timestamp, "comment": _or_dash}

    def pre_add(self, item):
        # The reviewer is the authenticated user (electronic signature) and the
        # verdict pins the reviewed version (frozen for baseline-based reviews).
        item.reviewer = g.user
        try:
            validate_verdict_scope(item.review, item.artefact)
        except ValueError as exc:
            raise Exception(str(exc))
        version = review_artefact_version(item.review, item.artefact)
        if version is not None:
            item.artefact_version_id = version.id


class ReviewAssignmentView(ModelView):
    datamodel = SQLAInterface(ReviewAssignment)
    page_size = PAGE_SIZE
    list_title = _("Reviewers")
    show_title = _("Reviewer")
    add_title = _("Assign a reviewer")
    edit_title = _("Edit reviewer assignment")
    search_columns = ["review", "reviewer", "role_in_review"]
    list_columns = ["reviewer", "role_in_review", "response_status"]
    show_columns = ["review", "reviewer", "role_in_review", "response_status"]
    add_fieldsets = [
        (_("Assignment"), {
            "fields": ["review", "reviewer", "role_in_review", "response_status"]
        })
    ]
    edit_fieldsets = [
        (_("Assignment"), {"fields": ["role_in_review", "response_status"]})
    ]
    label_columns = {
        "reviewer": _("Reviewer"),
        "role_in_review": _("Role"),
        "response_status": _("Response"),
    }
    description_columns = {
        "role_in_review": _("Chair, Reviewer, Scribe — free text for now."),
        "response_status": _("Open / Accepted / Declined — free text for now."),
    }


class CommentView(ModelView):
    """Threaded discussion attached to a review or an artefact."""

    datamodel = SQLAInterface(Comment)
    page_size = PAGE_SIZE
    list_title = _("Discussion")
    show_title = _("Comment")
    add_title = _("Add comment")
    edit_title = _("Edit comment")
    search_columns = ["review", "artefact", "body", "resolved"]
    list_columns = ["created_by", "body", "resolved", "created_on"]
    show_fieldsets = [
        (_("Comment"), {"fields": ["body", "resolved"]}),
        (_("Belongs to"), {
            "fields": ["review", "artefact", "artefact_version", "parent"]
        }),
        (_("Record"), {"fields": ["created_by", "created_on"], "expanded": False}),
    ]
    add_fieldsets = [
        (_("Belongs to"), {"fields": ["review", "artefact", "parent"]}),
        (_("Comment"), {"fields": ["body", "resolved"]}),
    ]
    edit_fieldsets = [(_("Comment"), {"fields": ["body", "resolved"]})]
    label_columns = {
        "artefact_version": _("Reviewed version"),
        "body": _("Comment"),
        "resolved": _("Resolved"),
        "parent": _("Replying to"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "body": _("Say what should change and why."),
        "resolved": _("Tick once the point has been addressed."),
        "review": _("Leave empty to comment on the artefact itself."),
        "artefact": _("Set this, or the review — a comment needs something to belong to."),
        "parent": _("Pick a comment only to reply to it."),
    }
    formatters_columns = {
        "created_on": _timestamp,
        "resolved": _resolved,
        "body": _or_dash,
    }

    def pre_add(self, item):
        # A comment with neither a review nor an artefact belongs to nothing and
        # would be invisible on every screen - refuse it with an explanation.
        if item.review is None and item.artefact is None:
            raise Exception(
                "Choose the review or the artefact this comment belongs to. "
                "Open the Discussion tab of the review or artefact you want to "
                "comment on, then add the comment there."
            )
        # Pin artefact comments to the version under review (frozen baseline
        # version for baseline-based reviews), never the live row.
        if item.review is not None and item.artefact is not None:
            version = review_artefact_version(item.review, item.artefact)
            if version is not None:
                item.artefact_version_id = version.id


class ApprovalView(ModelView):
    """Electronic approval record: evidence, never a form.

    Signatures are made with the review page's 'Sign off (approve)' / 'Sign with
    dissent' buttons, so the reviewer can never pick the wrong review from a
    dropdown and the signing user is always the one who clicked (FR-P7).
    """

    datamodel = SQLAInterface(Approval)
    page_size = PAGE_SIZE
    list_title = _("Signatures")
    show_title = _("Signature")
    base_permissions = READ_ONLY
    exclude_route_methods = {"add", "edit", "delete", "download"}
    search_columns = ["review", "reviewer", "decision"]
    list_columns = ["review", "reviewer", "decision", "created_on"]
    show_columns = ["review", "reviewer", "decision", "comment", "created_on"]
    label_columns = {"decision": _("Your decision"), "comment": _("Notes")}
    description_columns = {
        "review": _("The review this signature belongs to."),
        "decision": _("The recommendation the signer made at the time."),
        "comment": _(
            "Recorded with the account name and the time as the electronic "
            "signature - it cannot be edited afterwards."
        ),
    }
    formatters_columns = {"created_on": _timestamp, "comment": _or_dash}


# ---------------------------------------------------------------------------
# Phase 2 — Change Management (FR-8)
# ---------------------------------------------------------------------------
class ChangeRequestView(SingleRecordActionMixin, ModelView):
    datamodel = SQLAInterface(ChangeRequest)
    page_size = PAGE_SIZE
    related_views = []
    list_title = _("Change requests")
    show_title = _("Change request")
    add_title = _("Add change request")
    edit_title = _("Edit change request")
    base_order = ("created_on", "desc")
    list_columns = ["title", "project", "status", "priority", "requested_by", "created_on"]
    search_columns = ["title", "project", "status", "priority", "requested_by"]
    show_fieldsets = [
        (_("Change request"), {
            "fields": ["title", "project", "status", "priority", "description"]
        }),
        (_("Who asked"), {"fields": ["requested_by"]}),
        (_("Record"), {"fields": ["created_by", "created_on"], "expanded": False}),
    ]
    add_fieldsets = [
        (_("Change request"), {
            "fields": ["project", "title", "description", "priority"]
        })
    ]
    edit_fieldsets = [
        (_("Change request"), {"fields": ["title", "description", "priority"]})
    ]
    label_columns = {
        "requested_by": _("Requested by"),
        "status": _("State"),
        "description": _("What and why"),
        **AUDIT_LABELS,
    }
    description_columns = {
        "title": _("One line that says what would change."),
        "description": _(
            "The problem to solve and the artefacts affected — analysis happens in "
            "the change items, not here."
        ),
        "priority": _("How urgently the decision is needed."),
    }
    formatters_columns = {
        **AUDIT_FORMATTERS,
        "description": _or_dash,
    }

    def pre_add(self, item):
        # Requested-by is the authenticated user.
        item.requested_by = g.user

    def _transition(self, item, target):
        """Guarded state change + a flash, always returning to the request page."""
        if can_transition(item.status, target):
            item.status = target
            db.session.commit()
            flash(_("Change request moved to %(state)s.", state=target.value), "success")
        else:
            flash(_("Cannot move change request from %(cur)s to %(tgt)s.",
                    cur=item.status.value, tgt=target.value), "danger")
        return show_redirect(self, item.id)

    @action(
        "advance",
        _("Next step"),
        _("Move this change request one step along the path Submitted - Analysed - "
          "Approved - Implemented - Verified - Closed?"),
        "fa-arrow-right",
        multiple=False,
    )
    def advance(self, item):
        """One button replaces five.

        The path is linear, so exactly one move is legal at any time: the old
        screen showed Analyse/Approve/Implement/Verify/Close together and four of
        them could only ever fail.
        """
        target = next_change_state(item.status)
        if target is None:
            flash(
                _("Nothing left to do - this change request is %(state)s.",
                  state=item.status.value),
                "info",
            )
            return show_redirect(self, item.id)
        return self._transition(item, target)


class ChangeSetItemView(ModelView):
    datamodel = SQLAInterface(ChangeSetItem)
    page_size = PAGE_SIZE
    list_title = _("Change items")
    show_title = _("Change item")
    add_title = _("Propose a change")
    edit_title = _("Edit proposed change")
    search_columns = ["change_request", "artefact", "action", "applied"]
    list_columns = ["change_request", "artefact", "action", "applied"]
    show_columns = [
        "change_request", "artefact", "action", "proposed_change", "applied"
    ]
    add_fieldsets = [
        (_("Proposed change"), {
            "fields": ["change_request", "artefact", "action", "proposed_change"]
        })
    ]
    edit_fieldsets = [
        (_("Proposed change"), {"fields": ["action", "proposed_change", "applied"]})
    ]
    label_columns = {
        "action": _("Action"),
        "proposed_change": _("Proposed text"),
        "applied": _("Applied"),
    }
    description_columns = {
        "artefact": _("The artefact this change touches (must be in the request's project)."),
        "action": _("Add, Update or Delete."),
        "proposed_change": _(
            "The new wording (or the reason for the deletion) — applied to the "
            "artefact by hand for now."
        ),
        "applied": _("Tick once the artefact has actually been changed."),
    }
    formatters_columns = {"applied": _applied, "proposed_change": _or_dash}


# ---------------------------------------------------------------------------
# Baselines and entries (Phase 1)
# ---------------------------------------------------------------------------
class BaselineEntryView(ModelView):
    """Frozen snapshot contents shown as a tab on the Baseline detail page."""

    datamodel = SQLAInterface(BaselineEntry)
    page_size = PAGE_SIZE
    list_title = _("Contents")
    show_title = _("Frozen artefact version")
    base_permissions = READ_ONLY
    exclude_route_methods = {"add", "edit", "delete", "download"}
    search_columns = ["artefact"]
    list_columns = [
        "artefact_version.title",
        "artefact_version.kind",
        "artefact_version.version_number",
        "artefact_version.status",
        "artefact_version.priority",
    ]
    show_columns = [
        "artefact_version.title",
        "artefact_version.kind",
        "artefact_version.content",
        "artefact_version.status",
        "artefact_version.priority",
        "artefact_version.version_number",
    ]
    label_columns = {
        "artefact_version.title": _("Title"),
        "artefact_version.kind": _("Kind"),
        "artefact_version.version_number": _("Version"),
        "artefact_version.status": _("Status"),
        "artefact_version.priority": _("Priority"),
        "artefact_version.content": _("Content (frozen)"),
    }


class BaselineView(SingleRecordActionMixin, ModelView):
    """Immutable project baseline capturing the current approved artefact versions."""

    datamodel = SQLAInterface(Baseline)
    page_size = PAGE_SIZE
    related_views = [BaselineEntryView]
    list_title = _("Baselines")
    show_title = _("Baseline")
    add_title = _("Create baseline")
    base_permissions = ["can_add", "can_list", "can_show"]
    exclude_route_methods = {"edit", "delete", "download"}
    base_order = ("created_on", "desc")
    search_columns = ["name", "project", "created_by"]
    list_columns = ["name", "project", "created_by", "created_on"]
    # Name is optional here: blank gets the same date-stamped name the project
    # page's one-click action uses, so both doors produce comparable identifiers.
    # The extra field replaces the model field, so its description carries the hint.
    add_form_extra_fields = {
        "name": StringField(
            _("Baseline name"), description=BASELINE_NAME_HINT
        )
    }
    show_fieldsets = [
        (_("Baseline"), {"fields": ["name", "project", "description"]}),
        (_("Record"), {"fields": ["created_by", "created_on"], "expanded": False}),
    ]
    add_fieldsets = [(_("Baseline"), {"fields": ["project", "name", "description"]})]
    label_columns = {"name": _("Baseline name"), **AUDIT_LABELS}
    formatters_columns = {"created_on": _timestamp, "description": _or_dash}
    description_columns = {
        "project": _("Everything Approved in this project is captured."),
        "description": _("What this baseline is for (optional)."),
    }

    def pre_add(self, item):
        if not (item.name or "").strip():
            item.name = default_baseline_name(item.project)

    @action(
        "create_review",
        _("Create review from baseline"),
        _("Create a review frozen over this baseline's approved artefacts? "
          "(verdicts will tie to the baseline versions)"),
        "fa-clipboard-check",
        multiple=False,
    )
    def create_review(self, item):
        """One-click DNG-style review-from-baseline."""
        review = create_review_from_baseline(item)
        flash(
            _("Review '%(title)s' created from baseline '%(base)s'. Assign "
              "reviewers to start.", title=review.title, base=item.name),
            "success",
        )
        return show_redirect(self, item.id)

    def post_add(self, item):
        count = snapshot_baseline(item)
        if not flash_baseline_result(item, count):
            # An immutable, empty baseline is a trap for the next person to open
            # it, so refuse it here exactly as the project page does.
            db.session.delete(item)
            db.session.commit()


# ---------------------------------------------------------------------------
# Landing page (C10) - the governance loop at a glance
# ---------------------------------------------------------------------------
def landing_stats():
    """Counters for the landing page: one aggregate query per figure."""

    def count(query):
        return query.scalar() or 0

    linked_ids = (
        db.session.query(ArtefactRelationship.source_id)
        .union(db.session.query(ArtefactRelationship.target_id))
        .subquery()
    )
    return {
        "projects": count(db.session.query(func.count(Project.id))),
        "artefacts": count(db.session.query(func.count(Artefact.id))),
        "draft_artefacts": count(
            db.session.query(func.count(Artefact.id)).filter(
                Artefact.status == RequirementState.DRAFT
            )
        ),
        "approved_artefacts": count(
            db.session.query(func.count(Artefact.id)).filter(
                Artefact.status == RequirementState.APPROVED
            )
        ),
        "baselines": count(db.session.query(func.count(Baseline.id))),
        "open_reviews": count(
            db.session.query(func.count(Review.id)).filter(
                Review.status.in_(OPEN_REVIEW_STATES)
            )
        ),
        "open_changes": count(
            db.session.query(func.count(ChangeRequest.id)).filter(
                ChangeRequest.status != ChangeState.CLOSED
            )
        ),
        "suspect_links": count(
            db.session.query(func.count(ArtefactRelationship.id)).filter(
                ArtefactRelationship.is_suspect.is_(True)
            )
        ),
        "orphan_artefacts": count(
            db.session.query(func.count(Artefact.id)).filter(
                ~Artefact.id.in_(linked_ids)
            )
        ),
    }


class HomeView(IndexView):
    """What exists, and what needs doing next (replaces the stock welcome page).

    Registered through the ``FAB_INDEX_VIEW`` config key. Public like F.A.B.'s own
    index (no ``@has_access``), so an anonymous visitor gets the same welcome and
    the sign-in prompt in the navbar.
    """

    index_template = "reqman_index.html"

    @expose("/")
    def index(self):
        self.update_redirect()
        return self.render_template(
            self.index_template,
            appbuilder=self.appbuilder,
            stats=landing_stats(),
            filters={
                "draft_artefacts": FILTER_DRAFT_ARTEFACTS,
                "approved_artefacts": FILTER_APPROVED_ARTEFACTS,
                "suspect_links": FILTER_SUSPECT_LINKS,
                "active_reviews": FILTER_ACTIVE_REVIEWS,
                "open_changes": FILTER_OPEN_CHANGES,
                "orphan_artefacts": FILTER_ORPHANS,
            },
        )


# ---------------------------------------------------------------------------
# Registration (idempotent — used by create_app, which may run more than once
# in the test suite)
# ---------------------------------------------------------------------------
def register_views(appbuilder):
    """Register every view, then build the menu in workflow order (C8/C9).

    The order of the ``add_view``/``add_link`` calls *is* the menu order, so it
    reads as the governance loop: capture -> govern -> analyse -> overview ->
    configure. Re-running it (the test suite boots the app more than once) skips
    registration but still prunes stale permissions.
    """
    already_registered = any(
        getattr(v, "__name__", None) == "ArtefactView"
        or getattr(v, "__class__", type(v)).__name__ == "ArtefactView"
        for v in appbuilder.baseviews
    )
    if not already_registered:
        _register_menu(appbuilder)

    # Renaming a view class, a menu entry or a row action leaves its permissions
    # behind. Prune them here rather than calling appbuilder.security_cleanup():
    # that one asks to delete permissions while role links still exist, refuses,
    # and logs "Refused to delete permission view, assoc with role exists" on
    # every single boot (see app/security.py).
    prune_stale_permissions(appbuilder)


def _register_menu(appbuilder):
    """Menu entries in workflow order, then the no-menu helper (tab) views."""
    # --- Capture ---------------------------------------------------------
    appbuilder.add_view(
        ProjectView,
        "Projects",
        label=_("Projects"),
        icon="fa-folder-open-o",
        category=CAT_REQUIREMENTS,
        category_icon="fa-cubes",
    )
    appbuilder.add_view(
        ArtefactView,
        "Artefacts",
        label=_("Artefacts"),
        icon="fa-file-text-o",
        category=CAT_REQUIREMENTS,
    )
    appbuilder.add_view(
        RelationshipView,
        "Trace Links",
        label=_("Trace Links"),
        icon="fa-code-fork",
        category=CAT_REQUIREMENTS,
    )
    appbuilder.add_link(
        "Draft artefacts",
        href=FILTER_DRAFT_ARTEFACTS,
        label=_("Draft artefacts"),
        icon="fa-pencil",
        category=CAT_REQUIREMENTS,
    )

    # --- Govern ----------------------------------------------------------
    appbuilder.add_view(
        BaselineView,
        "Baselines",
        label=_("Baselines"),
        icon="fa-lock",
        category=CAT_GOVERNANCE,
        category_icon="fa-shield",
    )
    appbuilder.add_view(
        ReviewView,
        "Reviews",
        label=_("Reviews"),
        icon="fa-check-square-o",
        category=CAT_GOVERNANCE,
    )
    appbuilder.add_link(
        "Active reviews",
        href=FILTER_ACTIVE_REVIEWS,
        label=_("Active reviews"),
        icon="fa-play",
        category=CAT_GOVERNANCE,
    )
    appbuilder.add_view(
        ChangeRequestView,
        "Change Requests",
        label=_("Change Requests"),
        icon="fa-pencil-square-o",
        category=CAT_GOVERNANCE,
    )
    appbuilder.add_link(
        "Open change requests",
        href=FILTER_OPEN_CHANGES,
        label=_("Open change requests"),
        icon="fa-envelope-open-o",
        category=CAT_GOVERNANCE,
    )

    # --- Analyse ---------------------------------------------------------
    appbuilder.add_view(
        TraceGraphView,
        "Traceability Graph",
        label=_("Traceability Graph"),
        icon="fa-share-alt",
        category=CAT_ANALYSIS,
        category_icon="fa-search",
    )
    appbuilder.add_view(
        ImpactAnalysisView,
        "Impact Report",
        label=_("Impact Report"),
        icon="fa-sitemap",
        category=CAT_ANALYSIS,
    )
    appbuilder.add_link(
        "Suspect trace links",
        href=FILTER_SUSPECT_LINKS,
        label=_("Suspect trace links"),
        icon="fa-warning",
        category=CAT_ANALYSIS,
    )
    appbuilder.add_link(
        "Orphan report",
        href=FILTER_ORPHANS,
        label=_("Orphan report"),
        icon="fa-question-circle",
        category=CAT_ANALYSIS,
    )

    # --- Overview --------------------------------------------------------
    appbuilder.add_view(
        ArtefactDistributionChartView,
        "Artefact Mix",
        label=_("Artefact Mix"),
        icon="fa-pie-chart",
        category=CAT_DASHBOARD,
        category_icon="fa-bar-chart",
    )
    appbuilder.add_view(
        ArtefactPerProjectChartView,
        "Artefacts per Project",
        label=_("Artefacts per Project"),
        icon="fa-bar-chart",
        category=CAT_DASHBOARD,
    )

    # --- Configure -------------------------------------------------------
    appbuilder.add_view(
        RelationshipTypeView,
        "Link Types",
        label=_("Link Types"),
        icon="fa-wrench",
        category=CAT_ADMINISTRATION,
        category_icon="fa-wrench",
    )

    # No-menu helper views (reachable only as related tabs / detail pages)
    appbuilder.add_view_no_menu(ArtefactVersionView())
    appbuilder.add_view_no_menu(ArtefactOutgoingLinkView())
    appbuilder.add_view_no_menu(ArtefactIncomingLinkView())
    appbuilder.add_view_no_menu(BaselineEntryView())
    appbuilder.add_view_no_menu(ReviewVerdictView())
    appbuilder.add_view_no_menu(ReviewAssignmentView())
    appbuilder.add_view_no_menu(CommentView())
    appbuilder.add_view_no_menu(ApprovalView())
    appbuilder.add_view_no_menu(ChangeSetItemView())


# Patch related views whose twin classes are defined later in this module.
# Project show page = project workspace (artefacts, baselines, reviews, CRs).
ProjectView.related_views = [ArtefactView, BaselineView, ReviewView, ChangeRequestView]
ArtefactView.related_views = [
    ArtefactVersionView,
    CommentView,
    ArtefactOutgoingLinkView,
    ArtefactIncomingLinkView,
]
ReviewView.related_views = [ReviewVerdictView, ReviewAssignmentView, CommentView, ApprovalView]
ChangeRequestView.related_views = [ChangeSetItemView]