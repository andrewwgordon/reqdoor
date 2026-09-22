"""Domain model: every table REQMan persists, plus the enums that drive its workflows.

This module is deliberately schema-only. It contains **no** business rules: the
invariants that make the data trustworthy live in :mod:`app.services` (so that no
call site - view hook, action, script or test - can bypass them) and the guarded
state transitions are surfaced by row actions in :mod:`app.views`. The prose here
explains each rule and names the function that enforces it; the full analysis, the
ER/state diagrams and the verified list of schema risks are in
`docs/domain_model.md`.

Four layers, read top to bottom
------------------------------
1. **Scope**      ``Project``                      everything below is project-scoped.
2. **Content**    ``Artefact`` + ``ArtefactVersion``  one live row per managed object and
                 an append-only snapshot of every real change (FR-P3/FR-P4).
3. **Structure**  ``RelationshipType`` + ``ArtefactRelationship``  typed, validated, directed
                 trace links with a suspect flag (FR-P5/FR-P6).
4. **Governance** ``Baseline``/``BaselineEntry``, ``Review``/``ReviewAssignment``/``ReviewVerdict``/
                 ``Approval``/``Comment``, ``ChangeRequest``/``ChangeSetItem``  freeze content,
                 judge it, and change it under control (FR-P4, FR-P7, FR-P8).

The idea the whole schema is built around
----------------------------------------
**Evidence never points at a live row.** A baseline entry, a review verdict and a review
comment all reference an ``ArtefactVersion``, so a decision stays about *what was actually
reviewed* even after the artefact moves on. See :func:`app.services.review_artefact_version`.

Conventions worth knowing before editing
---------------------------------------
* ``str, Enum`` + ``SAEnum(..., native_enum=False)`` stores the **member name**
  (``'SYSTEM_REQ'``, ``'APPROVED'``) in a plain ``VARCHAR``; the UI shows ``.value``
  (``System Requirement``, ``Approved``). Anything speaking SQL directly - reports,
  imports, the future REST API - must use the stored names.
* ``create_all()`` is the only schema mechanism today (no Alembic yet), so a new column
  or constraint here is not applied to an existing database automatically.
* SQLite does **not** enforce foreign keys unless ``PRAGMA foreign_keys=ON`` is set per
  connection (it is not), and ``native_enum=False`` emits no CHECK constraint. Until both
  are addressed, several rules below are guaranteed only by :mod:`app.services`.
* ``AuditMixin`` supplies ``created_on/changed_on/created_by/changed_by`` on most tables;
  ``BaselineEntry``, ``ReviewAssignment`` and ``ChangeSetItem`` deliberately omit it today,
  which is a known auditability gap rather than an oversight to copy.
"""

from enum import Enum

from flask_appbuilder import Model
from flask_appbuilder.models.mixins import AuditMixin
from flask_appbuilder.security.sqla.models import User
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import backref, relationship


class RequirementState(str, Enum):
    """Lifecycle of a requirement-like artefact (FR-P2), in workflow order.

    Stored on both ``Artefact.status`` (the live mirror) and
    ``ArtefactVersion.status`` (the historical fact), because a status is part of what a
    version freezes.

    Only three of these eight states have a UI door today: ``DRAFT`` (the default for a
    new artefact - status is not even on the add form), ``APPROVED`` (the *Approve
    artefact* action) and ``OBSOLETE`` (the *Mark obsolete* action). The rest are set by
    editing ``status`` directly: unlike :class:`ReviewState` and :class:`ChangeState`
    there is no ``REQUIREMENT_TRANSITIONS`` table in :mod:`app.services`, so nothing
    prevents Draft -> Released. That gap is tracked in `docs/domain_model.md` §7.

    ``APPROVED`` is the load-bearing value: :func:`app.services.snapshot_baseline` only
    baselines an artefact whose **latest version** is Approved, which is what makes the
    Approve action the gate into configuration control (FR-P4).
    """

    DRAFT = "Draft"
    PROPOSED = "Proposed"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"  # baseline-eligible; set via the Approve action, which versions
    IMPLEMENTED = "Implemented"
    VERIFIED = "Verified"
    RELEASED = "Released"
    OBSOLETE = "Obsolete"  # retired, never deleted: baselines and links still refer to it


class Priority(str, Enum):
    """Importance of an artefact or urgency of a change decision.

    Carried by ``Artefact.priority``, ``ArtefactVersion.priority`` (a version pins it, so
    a re-triage is visible in history) and ``ChangeRequest.priority``. No ordering is
    expressible in the database: the enum's declaration order is not a sort key, so the
    lists sort by project/title and priority is a filter, not an order.
    """

    LOW = "Low"
    MEDIUM = "Medium"  # the column default for a new artefact and a new change request
    HIGH = "High"
    CRITICAL = "Critical"


class ArtefactKind(str, Enum):
    """The taxonomy of managed engineering objects (FR-P2).

    A kind is a *label*, not a class: every kind is one ``Artefact`` table, so adding a
    kind is a code change here rather than a data change. ``RelationshipType`` restricts
    which kinds may sit at each end of a link, but those rules are stored as this enum's
    **member names** (``'["SYSTEM_REQ", "SOFTWARE_REQ"]'`` as JSON text) and are compared
    against ``kind.name`` by :func:`app.services._validate_kinds`.
    """

    STAKEHOLDER_REQ = "Stakeholder Requirement"  # column default
    SYSTEM_REQ = "System Requirement"
    SOFTWARE_REQ = "Software Requirement"
    USE_CASE = "Use Case"
    RISK = "Risk"
    TEST_CASE = "Test Case"  # typically the far end of a 'Verifies' link
    FEATURE = "Feature"
    USER_STORY = "User Story"
    DEFECT = "Defect"


class ReviewState(str, Enum):
    """Formal review workflow (FR-P7).

    Legal moves live in ``services.REVIEW_TRANSITIONS`` and the single-forward path in
    ``services.REVIEW_ADVANCE``, so the UI needs one *Next step* button instead of a
    button per state; ``ACTIVE -> COMMENT_RESOLUTION`` is the one branch that path cannot
    take and gets its own *Send back for comment* action. Consequences enforced in code:

    * a review may be created only as ``PLANNED`` (``status`` is not on the add form),
    * signatures are accepted while the state is in ``services.REVIEW_DECIDABLE_FROM``
      (``ACTIVE``, ``COMMENT_RESOLUTION``) and refused otherwise,
    * ``CLOSED`` is terminal - no successor, so the record is inert evidence.

    Review content itself is decided per artefact by :class:`ReviewVerdict`; the progress
    shown against these states is computed, never stored - see ``Review.progress``.
    """

    PLANNED = "Planned"  # default: scope and reviewers still being arranged
    ACTIVE = "Active"  # verdicts and signatures accepted
    COMMENT_RESOLUTION = "Comment Resolution"  # authors are addressing raised comments
    APPROVED = "Approved"  # decided; awaiting closure
    CLOSED = "Closed"  # terminal


class ChangeState(str, Enum):
    """Change-control workflow (FR-8): a strictly linear path, one move per button.

    ``services.CHANGE_TRANSITIONS`` defines what is legal and
    ``services.CHANGE_ADVANCE`` what the *Next step* action does from each state; because
    the path is linear the two agree exactly, which is asserted by tests.

    Note the absence of a Rejected/Cancelled state: a request that is refused can only be
    pushed on to ``CLOSED``, so dissent is not representable in the data (or in the audit
    trail it feeds). That is a modelling gap, not a UI one.

    ``CLOSED`` is terminal, and nothing here records *which* versions the request produced
    - ``ChangeSetItem.applied`` is a hand-ticked flag. See ``docs/domain_model.md`` F1/2.4.
    """

    SUBMITTED = "Submitted"  # default for a new request
    ANALYSED = "Analysed"
    APPROVED = "Approved"
    IMPLEMENTED = "Implemented"
    VERIFIED = "Verified"
    CLOSED = "Closed"  # terminal


class Decision(str, Enum):
    """A reviewer's judgement, reused by two different kinds of record.

    * ``ReviewVerdict.decision`` - per artefact, one current position per
      (review, reviewer, artefact) enforced by ``uq_review_verdict``, re-recording replaces.
    * ``Approval.decision`` - per review, the electronic signature; ``services.record_approval``
      keeps one current position per (review, reviewer) in code, which is not yet a
      database constraint.
    """

    APPROVE = "Approve"
    REJECT = "Reject"
    ABSTAIN = "Abstain"


class ChangeAction(str, Enum):
    """What a :class:`ChangeSetItem` proposes to do to one artefact.

    Documentation only at this stage: nothing applies these to the artefact yet, so the
    proposed wording is text alongside the item rather than an ``ArtefactVersion`` it
    created (FR-P8's later phase). ``ADD``/``DELETE`` therefore have no effect on row
    existence, and ``column default`` is ``UPDATE`` - the most common case.
    """

    ADD = "Add"
    UPDATE = "Update"  # column default
    DELETE = "Delete"


class Project(Model, AuditMixin):
    """Top-level container for scoped requirements (FR-P1).

    Everything an engineer works on hangs off a project: artefacts, baselines, reviews,
    change requests and trace links all carry ``project_id``, which is why trace links
    may not cross projects (:func:`app.services.validate_relationship`) and why a
    baseline is defined as "this project's approved versions".

    ``project_code`` is the human handle and doubles as the prefix of an auto-generated
    baseline name (``"HX4 BL 2026-09-22"``, see
    :func:`app.services.default_baseline_name`), so it is unique and effectively
    immutable - renaming one orphans nothing in the data but breaks every document that
    quotes it.

    Lifecycle: there is deliberately no archive/status column, and no ``ondelete``
    behaviour on the children's FKs, so deleting a project that still has artefacts
    fails silently (nothing is removed and nothing is explained). See
    ``docs/domain_model.md`` F3 before adding a delete affordance here.
    """

    __tablename__ = "project"
    id = Column(Integer, primary_key=True)
    project_code = Column(String(30), unique=True, nullable=False)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)

    def __repr__(self):
        # What every dropdown, picker and list cell shows for a project.
        return f"[{self.project_code}] {self.name}"


class Artefact(Model, AuditMixin):
    """A managed engineering object: requirement, story, risk, test case... (FR-P2).

    One table for every :class:`ArtefactKind`; a new kind is an enum change, not a schema
    change. The row is *live* working data - what the editor form reads and writes.

    The mirror invariant (FR-P3)
    ----------------------------
    ``kind``, ``title``, ``content``, ``status`` and ``priority`` are a **copy of the
    artefact's latest version**, so the artefact page and list always show version N. The
    write path keeps them in step: every save runs through
    :func:`app.services.create_version` from ``ArtefactView.post_add``/``post_update``
    (a save with no changed field adds no version). Two consequences to keep in mind:

    * the duplication is real, so ``services.reconcile_artefact()`` exists to repair drift
      introduced by any write that bypassed versioning (it is currently invoked from
      ``ArtefactView._show``, i.e. on a GET - see docs/domain_model.md F6);
    * ``project_id`` has no ``ondelete``, so an artefact outlives nothing here: deleting it
      cascades its versions (ORM ``delete-orphan``) while baselines, trace links, verdicts
      and change items that reference it are guarded only in ``ArtefactView.pre_delete``.

    No ordering or hierarchy: ``parent_id``/module folders are FR-2 later-phase work, and
    there is no human reference like ``SPEC-0042`` - ``__repr__`` shows the database id.
    """

    __tablename__ = "artefact"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    project = relationship("Project", backref=backref("artefacts"))
    kind = Column(
        SAEnum(ArtefactKind, native_enum=False),
        default=ArtefactKind.STAKEHOLDER_REQ,
        nullable=False,
    )
    title = Column(String(250), nullable=False)
    content = Column(Text, default="")  # plain text for now; rich text is a later phase
    status = Column(
        SAEnum(RequirementState, native_enum=False),
        default=RequirementState.DRAFT,  # a new artefact is always a Draft (FR-P2)
        nullable=False,
    )
    priority = Column(
        SAEnum(Priority, native_enum=False),
        default=Priority.MEDIUM,
        nullable=False,
    )

    @property
    def project_code(self) -> str:
        """Sortable project code for chart/grouping display.

        A model property, so F.A.B. can list it but cannot ``ORDER BY`` it at the SQL
        level - charts group by ``project_id`` and format the label afterwards instead.
        """
        return self.project.project_code if self.project is not None else ""

    def __repr__(self):
        # ``#<id> <title>`` is quoted by link lists, verdict pickers and orphan reports.
        return f"#{self.id} {self.title}"


class ArtefactVersion(Model, AuditMixin):
    """Immutable snapshot of an artefact at a point in time (FR-P3/FR-4).

    Every field that carries meaning is copied (``VERSIONED_FIELDS`` in
    :mod:`app.services`), plus ``change_summary`` saying why it exists - *Initial draft*,
    *Edited*, *Status set to Approved*, *Marked obsolete*, *Restored from version N*. The
    table is append-only: ``ArtefactVersionView`` exposes no add/edit/delete routes, and
    restoring an older version **records a new version** rather than rewriting history, so
    ``version_number`` strictly increases.

    Caveats a reader should know about
    ----------------------------------
    * ``version_number`` is a per-artefact ``max+1`` computed by
      :func:`app.services.next_version_number` - a read-modify-write with no lock and no
      ``UNIQUE(artefact_id, version_number)``, so concurrent saves can both claim the next
      number (docs/domain_model.md 1.3).
    * The payload columns are nullable here but NOT NULL on ``artefact``, and the column
      defaults are applied by the ORM only. A version row written outside the ORM could
      hold NULLs, which ``reconcile_artefact()`` would then copy onto the live row.
    * ``AuditMixin`` on this table is what makes the history attributable: ``created_by``
      is who made the change, and ``created_on`` when - that pairing is the audit trail.
    """

    __tablename__ = "artefact_version"
    id = Column(Integer, primary_key=True)
    artefact_id = Column(
        Integer, ForeignKey("artefact.id", ondelete="CASCADE"), nullable=False
    )
    artefact = relationship(
        "Artefact", backref=backref("versions", cascade="all, delete-orphan")
    )
    version_number = Column(Integer, nullable=False)  # per artefact, 1..N, never reused
    kind = Column(SAEnum(ArtefactKind, native_enum=False))
    title = Column(String(250), default="")
    content = Column(Text, default="")
    status = Column(SAEnum(RequirementState, native_enum=False))
    priority = Column(SAEnum(Priority, native_enum=False))
    change_summary = Column(String(250), default="")

    def __repr__(self):
        return f"v{self.version_number}"


class Baseline(Model, AuditMixin):
    """Configuration-controlled snapshot of a project's approved artefact versions (FR-P6).

    A baseline names a state of the project so that reviews, releases and audits can refer
    to exactly the same content again. Both creation doors (the project page's one-click
    action and *Baselines > Add*) funnel into
    :func:`app.services.snapshot_baseline`, so the capture rule lives in one place: only
    artefacts whose **latest version** is ``RequirementState.APPROVED`` are entered;
    anything else is skipped and reported to the user by name.

    Immutability is enforced by the UI, not the schema: ``BaselineView`` disables
    ``edit``/``delete``, and ``BaselineEntry``'s ``ondelete="CASCADE"`` is therefore
    unreachable from the application. The consequence is that baselines accumulate - there
    is no status, supersede link or archive flag to say which one is current
    (docs/domain_model.md F9).
    """

    __tablename__ = "baseline"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    project = relationship("Project", backref=backref("baselines"))
    name = Column(String(150), nullable=False)  # unique per project by convention, not by DB
    description = Column(Text, nullable=True)

    def __repr__(self):
        return f"{self.name}"


class BaselineEntry(Model):
    """Frozen reference: an artefact as it stood at a specific version, in a baseline.

    The join row that turns a baseline from a name into a set of immutable facts, and the
    anchor for two rules:

    * ``uq_baseline_entry_artefact`` makes "one entry per artefact per baseline" a
      database guarantee (idempotence for ``snapshot_baseline``), and
    * a baseline's content never follows later edits, because ``artefact_version_id``
      points at the version captured at snapshot time - the FR-P5 acceptance loop asserts
      exactly this (re-edit A, reopen the baseline, it still shows A v2).

    ``artefact_id`` is derivable from ``artefact_version_id``; it is stored deliberately,
    so scope (``services.review_scope``), the deletion guard in ``ArtefactView.pre_delete``
    and the contents tab resolve without a join through versions. This table has no
    ``AuditMixin``, so *who* froze a version into a baseline is not recorded
    (docs/domain_model.md F4).
    """

    __tablename__ = "baseline_entry"
    id = Column(Integer, primary_key=True)
    baseline_id = Column(
        Integer, ForeignKey("baseline.id", ondelete="CASCADE"), nullable=False
    )
    baseline = relationship(
        "Baseline", backref=backref("entries", cascade="all, delete-orphan")
    )
    artefact_id = Column(Integer, ForeignKey("artefact.id"), nullable=False)
    artefact = relationship("Artefact", backref=backref("baseline_entries"))
    artefact_version_id = Column(
        Integer, ForeignKey("artefact_version.id"), nullable=False
    )
    artefact_version = relationship("ArtefactVersion")

    __table_args__ = (
        UniqueConstraint(
            "baseline_id", "artefact_id", name="uq_baseline_entry_artefact"
        ),
    )

    def __repr__(self):
        return (
            f"v{self.artefact_version.version_number} {self.artefact_version.title}"
        )


# ---------------------------------------------------------------------------
# Phase 2 — Traceability (FR-5)
# ---------------------------------------------------------------------------
class RelationshipType(Model, AuditMixin):
    """Defines a permissible trace link: Satisfies, Refines, Verifies... (FR-P5).

    Configuration shared across all projects (deliberately not project-scoped), so the link
    vocabulary is consistent enough to report on. ``name`` is the stable identifier used by
    imports and exports; ``display_name`` is what links and graph edges label themselves.

    Two fields are **advisory only** and should not be trusted as rules today:

    * ``source_kinds``/``target_kinds`` are JSON arrays of :class:`ArtefactKind` *member
      names*. :func:`app.services._validate_kinds` parses them leniently - malformed JSON or
      an empty list both mean "no restriction" - so a typo silently turns a rule off instead
      of rejecting every link. A child table of ``(type, end, kind)`` rows with a
      ``NOT NULL`` kind is the durable fix (docs/domain_model.md 3.3).
    * ``is_directional`` is displayed ("One way" / "Either way") and never read by
      :func:`app.services.validate_relationship`, so the reverse link is in fact still
      accepted. Either enforce it or drop the column: a decorative constraint misleads.
    """

    __tablename__ = "relationship_type"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # stable id, e.g. SATISFIES
    display_name = Column(String(100), nullable=False)      # e.g. Satisfies
    description = Column(Text, nullable=True)
    is_directional = Column(Boolean, default=True)  # displayed, never read by validation
    # Optional JSON arrays of ArtefactKind member names allowed on each end.
    source_kinds = Column(Text, nullable=True)
    target_kinds = Column(Text, nullable=True)

    def __repr__(self):
        return self.display_name or self.name


class ArtefactRelationship(Model, AuditMixin):
    """A persistent traceability link between two artefacts (FR-P5).

    A directed edge ``(source) --relationship_type--> (target)``. In the usual reading the
    source is the thing that depends on the target: a ``TEST_CASE`` *Verifies* a
    ``SYSTEM_REQ``, so the test case is the source.
    :func:`app.services.validate_relationship` refuses self-links, duplicates (backed by
    ``uq_trace_link`` below), cross-project links and kind-rule violations before insert,
    and ``RelationshipView.pre_add`` derives ``project_id`` from the source - so
    ``project_id`` agrees with its ends by code, not by constraint.

    Suspect-link semantics (DOORS style) all live in :mod:`app.services`:
    :func:`app.services.create_version` flags **every outgoing link** of a changed artefact
    with ``is_suspect=True``, :func:`app.services.clear_suspect` clears it via the *Mark as
    reviewed* action, and the artefact tabs, trace lists and impact report surface it.

    ``outgoing_relationships``/``incoming_relationships`` are the backrefs the two trace tabs
    bind to. Because both ends reference the same table, ``foreign_keys=`` has to be stated
    explicitly; and the *Traces (in)* tab must additionally pin its parent relation to
    ``target`` (``views._TargetSideInterface``), since F.A.B.'s ``get_related_fk`` returns
    the first relation it finds back to ``Artefact`` - always ``source``.
    """

    __tablename__ = "artefact_relationship"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    project = relationship("Project", backref=backref("artefact_relationships"))
    source_id = Column(Integer, ForeignKey("artefact.id"), nullable=False)
    source = relationship(
        "Artefact",
        foreign_keys=[source_id],
        backref=backref("outgoing_relationships"),
    )
    target_id = Column(Integer, ForeignKey("artefact.id"), nullable=False)
    target = relationship(
        "Artefact",
        foreign_keys=[target_id],
        backref=backref("incoming_relationships"),
    )
    relationship_type_id = Column(
        Integer, ForeignKey("relationship_type.id"), nullable=False
    )
    relationship_type = relationship("RelationshipType")
    is_suspect = Column(Boolean, default=False)  # ORM-side default; the column is nullable

    __table_args__ = (
        UniqueConstraint(
            "source_id", "target_id", "relationship_type_id", name="uq_trace_link"
        ),
    )

    def __repr__(self):
        return f"{self.source} {self.relationship_type} {self.target}"


# ---------------------------------------------------------------------------
# Phase 2 — Reviews (FR-7)
# ---------------------------------------------------------------------------
class Review(Model, AuditMixin):
    """Formal review of artefacts, optionally frozen over a baseline (FR-P7).

    A review is the container for judgement: :class:`ReviewAssignment` says who should take
    part, :class:`ReviewVerdict` records the per-artefact decision, :class:`Approval` the
    signature and :class:`Comment` the discussion. Status moves are guarded by
    ``services.REVIEW_TRANSITIONS`` and driven by the review page's *Next step* / *Send
    back for comment* actions, so no screen exposes the raw status field.

    ``basis_baseline_id`` is the pivot of the whole design:

    * **set** - scope is the baseline's entries (``services.review_scope``) and every
      verdict/comment pins the *frozen* version, so a decision can never drift onto a later
      edit (``services.review_artefact_version``);
    * **null** - a "stream" review over the project's live content, pinning whatever the
      latest version happens to be at decision time.

    Nothing in the schema ties a verdict's artefact to this review's project: that rule
    lives in :func:`app.services.validate_verdict_scope`, shared by ``record_verdict()``
    and ``ReviewVerdictView.pre_add`` so the two cannot diverge. Deleting a review cascades
    its verdicts, assignments, approvals and comments - i.e. it destroys the evidence trail,
    which is why *Close review* rather than delete is the intended end state
    (docs/domain_model.md 1.4).
    """

    __tablename__ = "review"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    project = relationship("Project", backref=backref("reviews"))
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        SAEnum(ReviewState, native_enum=False), default=ReviewState.PLANNED, nullable=False
    )
    due_date = Column(Date, nullable=True)  # when every reviewer should have decided
    # Optional baseline basis: when set, the review is frozen over that baseline's
    # snapshot (DNG review-from-baseline pattern) and verdicts/comments resolve to
    # the baseline's artefact versions instead of the live rows.
    basis_baseline_id = Column(Integer, ForeignKey("baseline.id"), nullable=True)
    basis_baseline = relationship(
        "Baseline",
        foreign_keys=[basis_baseline_id],
        backref=backref("basis_for_reviews"),
    )

    @property
    def progress(self) -> str:
        """Compact per-reviewer + overall decision progress for list/show display.

        Computed, never stored: :func:`app.services.review_progress` counts verdicts over
        the review's scope. Three consequences for anyone editing near this:

        * it must **not** appear in ``ReviewView.order_columns`` - a property is not an
          ``ORDER BY``-able column, and F.A.B. would generate a broken query (the view
          therefore lists its orderable columns explicitly);
        * it costs a query per row rendered, so a 25-row list over a 5k-artefact baseline
          performs 25 scans - docs/domain_model.md 4.3 proposes a ``review_item`` table to
          turn it into a lookup;
        * the empty string (no reviewers assigned yet) is displayed as "Not started - no
          reviewers yet" by ``ReviewView.formatters_columns``, because F.A.B. renders a
          blank cell as ``None``.
        """
        from app.services import review_progress

        data = review_progress(self)
        if not data["reviewers_count"]:
            return ""
        parts = [
            f"{name} {v['percent']}% ({v['decided']}/{v['required']})"
            for name, v in sorted(data["reviewers"].items())
        ]
        return f"overall {data['overall']}% · " + " · ".join(parts)

    def __repr__(self):
        return f"{self.title}"


class ReviewAssignment(Model):
    """A reviewer (and their role/response) invited to take part in a review (FR-P7).

    Assignments define who ``services.review_progress`` measures against, and are what a
    "reviews waiting on you" view would filter on. ``reviewer_id`` points at F.A.B.'s own
    ``ab_user`` - identity is shared with the security layer, which is why users are
    deactivated rather than deleted (this FK has no ``ondelete``).

    Both text columns are free-form today, so a roster can accumulate ``Chair``/``chair``/
    ``CHAIR`` and no query can safely interpret them; the proposed replacement is a
    ``ReviewerRole``/``ResponseStatus`` enum pair (docs/domain_model.md 2.2). Their defaults
    are applied by the ORM only, so a raw insert leaves them ``NULL``.

    Deliberately **no** ``AuditMixin`` right now: who appointed a reviewer, and when, goes
    unrecorded (docs/domain_model.md F4). Deleting the review cascades these rows.
    """

    __tablename__ = "review_assignment"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("review.id", ondelete="CASCADE"), nullable=False)
    review = relationship("Review", backref=backref("assignments", cascade="all, delete-orphan"))
    reviewer_id = Column(Integer, ForeignKey("ab_user.id"), nullable=False)
    reviewer = relationship(User, foreign_keys=[reviewer_id])
    role_in_review = Column(String(50), default="Reviewer")  # free text: Chair, Scribe, ...
    response_status = Column(String(50), default="Open")  # free text: Accepted, Declined

    def __repr__(self):
        return f"{self.reviewer} ({self.role_in_review})"


class ReviewVerdict(Model, AuditMixin):
    """Per-artefact review decision by one reviewer: Approve / Reject / Abstain (FR-P7).

    The verdict always pins the exact artefact version that was reviewed
    (``artefact_version``), so evidence stays traceable even if the artefact changes after
    the review - the FR-P7 acceptance case is literally "edit the artefact afterwards, the
    verdict still names the version that was reviewed". Which version that is gets resolved
    in one place, :func:`app.services.review_artefact_version`: the baseline's frozen
    version when the review has a basis, otherwise the latest version at decision time.

    Where each rule lives:

    * **one current decision per (review, reviewer, artefact)** is a *database* guarantee
      (``uq_review_verdict`` below), which is what lets :func:`app.services.record_verdict`
      be an upsert where "re-recording replaces the decision";
    * **what may be reviewed at all** is a *service* rule -
      :func:`app.services.validate_verdict_scope`, shared by ``record_verdict`` and
      ``ReviewVerdictView.pre_add`` so the UI and any future API cannot diverge (same
      project as the review; and for a baseline review, part of that baseline);
    * ``reviewer`` is always the signed-in user, never a form choice;
    * ``artefact_version_id`` is NOT NULL here - a verdict with nothing pinned would be
      meaningless evidence - unlike the nullable one on :class:`Comment`.

    Deleting the artefact is not blocked by the verdicts referencing it, so they can dangle
    (docs/domain_model.md F1).
    """

    __tablename__ = "review_verdict"
    id = Column(Integer, primary_key=True)
    review_id = Column(
        Integer, ForeignKey("review.id", ondelete="CASCADE"), nullable=False
    )
    review = relationship(
        "Review", backref=backref("verdicts", cascade="all, delete-orphan")
    )
    reviewer_id = Column(Integer, ForeignKey("ab_user.id"), nullable=False)
    reviewer = relationship(User, foreign_keys=[reviewer_id])
    artefact_id = Column(Integer, ForeignKey("artefact.id"), nullable=False)
    artefact = relationship("Artefact", foreign_keys=[artefact_id])
    artefact_version_id = Column(
        Integer, ForeignKey("artefact_version.id"), nullable=False
    )
    artefact_version = relationship("ArtefactVersion")
    decision = Column(SAEnum(Decision, native_enum=False), nullable=False)
    comment = Column(Text, default="")

    __table_args__ = (
        UniqueConstraint(
            "review_id", "reviewer_id", "artefact_id", name="uq_review_verdict"
        ),
    )

    def __repr__(self):
        return f"{self.decision.value} by {self.reviewer} on {self.artefact} @ {self.artefact_version}"


class Comment(Model, AuditMixin):
    """Threaded discussion attached to a review, an artefact, or both (FR-P7).

    One table serves two conversations:

    * the review's *Discussion* tab - ``review_id`` set, ``artefact_id`` optionally naming
      the artefact the point is about;
    * an artefact's *Discussion* tab - ``artefact_id`` set, ``review_id`` null;
    * ``parent_id`` with ``remote_side`` forms the reply thread (a one-to-many self
      reference, not a join table), giving the ``replies`` backref.

    When a comment targets an artefact **under review**, ``artefact_version_id`` pins the
    version being read, resolved exactly as for verdicts by
    :func:`app.services.review_artefact_version`; it stays nullable here, unlike on
    :class:`ReviewVerdict`, because a plain artefact comment has no review context to pin.

    Two sharp edges when touching this table:

    * the schema cannot say "a comment must belong to something" - all three FKs are
      nullable and only ``body`` is NOT NULL - so the invisible-comment rule is enforced
      only by ``CommentView.pre_add``; a CHECK constraint is the durable fix
      (docs/domain_model.md 2.5);
    * ``review_id``/``artefact_id``/``artefact_version_id`` are ``ON DELETE CASCADE``, so
      deleting a review or artefact takes its discussion with it, while deleting a *parent*
      comment (``parent_id``, which has no ``ondelete``) leaves its replies pointing at a
      missing row.
    """

    __tablename__ = "comment"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("review.id", ondelete="CASCADE"), nullable=True)
    review = relationship("Review", backref=backref("comments", cascade="all, delete-orphan"))
    artefact_id = Column(Integer, ForeignKey("artefact.id", ondelete="CASCADE"), nullable=True)
    artefact = relationship("Artefact", backref=backref("comments"))
    # When the comment targets an artefact under review, this pins the exact
    # version that was being reviewed (frozen baseline version for baseline reviews).
    artefact_version_id = Column(
        Integer, ForeignKey("artefact_version.id", ondelete="CASCADE"), nullable=True
    )
    artefact_version = relationship("ArtefactVersion")
    parent_id = Column(Integer, ForeignKey("comment.id"), nullable=True)  # self-reference: a reply thread, not a join table
    parent = relationship("Comment", remote_side=[id], backref=backref("replies"))
    body = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)  # "point addressed"; the column is nullable

    def __repr__(self):
        return f"#{self.id} {self.body[:60]}"


class Approval(Model, AuditMixin):
    """Electronic approval record for a review (FR-P7).

    Read-only evidence in the UI: ``ApprovalView`` exposes no add/edit/delete routes, and a
    signature is made with the review page's *Sign off (approve)* / *Sign with dissent*
    actions. That is what guarantees the signer is whoever clicked - ``reviewer_id`` comes
    from the authenticated user and cannot be filed against a review picked out of a
    dropdown - while ``AuditMixin`` supplies the timestamp, so the row *is* the signature.

    :func:`app.services.record_approval` keeps one current position per (review, reviewer)
    by replacing that reviewer's earlier row, so a double click cannot stack contradictory
    signatures. That idempotence is code-only: unlike ``uq_review_verdict`` there is no
    unique constraint here (docs/domain_model.md 1.3). Deleting the review cascades these
    rows, which is why *Close review* - not delete - is the intended end state.
    """

    __tablename__ = "approval"
    id = Column(Integer, primary_key=True)
    review_id = Column(Integer, ForeignKey("review.id", ondelete="CASCADE"), nullable=False)
    review = relationship("Review", backref=backref("approvals", cascade="all, delete-orphan"))
    reviewer_id = Column(Integer, ForeignKey("ab_user.id"), nullable=False)
    reviewer = relationship(User, foreign_keys=[reviewer_id])
    decision = Column(SAEnum(Decision, native_enum=False), nullable=False)
    comment = Column(Text, nullable=True)  # optional grounds stated by the signer

    def __repr__(self):
        return f"{self.decision.value} by {self.reviewer}"


# ---------------------------------------------------------------------------
# Phase 2 — Change Management (FR-8)
# ---------------------------------------------------------------------------
class ChangeRequest(Model, AuditMixin):
    """Change control record grouping modifications to artefacts (FR-P8).

    Head of a change package: the *what and why* lives here, the per-artefact *how* in
    :class:`ChangeSetItem`. Status walks the linear ``services.CHANGE_TRANSITIONS`` path
    (Submitted -> Analysed -> Approved -> Implemented -> Verified -> Closed), driven by the
    single *Next step* action; because the path is linear, one button is exactly one legal
    move at a time. There is no Rejected/Cancelled state, so a refused request can only be
    pushed on to Closed and dissent is not representable in the data.

    ``requested_by`` is the proposer, captured from the authenticated user in
    ``ChangeRequestView.pre_add``; ``created_by`` from ``AuditMixin`` records who typed the
    row. They are deliberately different facts - a chair may raise a request on someone
    else's behalf - and ``priority`` here means urgency of the *decision*, not of the
    artefact.
    """

    __tablename__ = "change_request"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=False)
    project = relationship("Project", backref=backref("change_requests"))
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        SAEnum(ChangeState, native_enum=False),
        default=ChangeState.SUBMITTED,
        nullable=False,
    )
    priority = Column(SAEnum(Priority, native_enum=False), default=Priority.MEDIUM)
    requested_by_id = Column(Integer, ForeignKey("ab_user.id"), nullable=False)
    requested_by = relationship(User, foreign_keys=[requested_by_id])

    def __repr__(self):
        return f"{self.title}"


class ChangeSetItem(Model):
    """A single proposed modification within a change request (FR-8).

    The proposal half of change control: one ``action`` (:class:`ChangeAction` - Add,
    Update or Delete) plus the ``proposed_change`` wording, against one artefact.

    ``applied`` is a **hand-ticked boolean**: nothing applies a proposal through
    ``create_version()`` yet, so no ``artefact_version_id`` records what the item produced.
    A request can therefore be Implemented and Closed while the artefact it described never
    changed, and the change record cannot be reconciled against the version history - adding
    that link is the intended shape (docs/domain_model.md 2.4).

    Also unenforced here: that the artefact belongs to the request's project, that only one
    item is raised per artefact, and that the named artefact keeps existing (deleting it
    leaves this row dangling). No ``AuditMixin``, so a proposal has no recorded author or
    time (docs/domain_model.md F4). Deleting the request cascades these rows.
    """

    __tablename__ = "change_set_item"
    id = Column(Integer, primary_key=True)
    change_request_id = Column(
        Integer, ForeignKey("change_request.id", ondelete="CASCADE"), nullable=False
    )
    change_request = relationship(
        "ChangeRequest", backref=backref("items", cascade="all, delete-orphan")
    )
    artefact_id = Column(Integer, ForeignKey("artefact.id"), nullable=False)
    artefact = relationship("Artefact")
    action = Column(SAEnum(ChangeAction, native_enum=False), default=ChangeAction.UPDATE)
    proposed_change = Column(Text, default="")  # new wording, applied by hand today
    applied = Column(Boolean, default=False)  # manual flag, not derived from a recorded version

    def __repr__(self):
        return f"{self.action.value} {self.artefact}"