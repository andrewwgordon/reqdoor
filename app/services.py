"""Business logic: immutable versioning, baseline snapshots, traceability,
reviews and change workflows.

All artefact mutation goes through these functions so that no call site (view hook,
action, test) can bypass the versioning invariant.
"""

import json
from datetime import date

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    Approval,
    Artefact,
    ArtefactRelationship,
    ArtefactVersion,
    Baseline,
    BaselineEntry,
    ChangeState,
    RelationshipType,
    RequirementState,
    Review,
    ReviewAssignment,
    ReviewState,
    ReviewVerdict,
)


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------
VERSIONED_FIELDS = ("kind", "title", "content", "status", "priority")
"""The artefact properties that are mirrored into every version snapshot."""


def differs_from_version(artefact, version: "ArtefactVersion | None") -> bool:
    """True when the live row is not a mirror of ``version`` (i.e. a real change).

    Decides whether a save deserves a new immutable version (a no-op Save must
    not inflate the history) and whether the display invariant (FR-P3) needs
    re-applying.
    """
    if version is None:
        return True
    return any(
        getattr(artefact, field) != getattr(version, field)
        for field in VERSIONED_FIELDS
    )


def next_version_number(artefact_id: int) -> int:
    """Return max(version_number)+1 for an artefact (first version = 1)."""
    last = (
        db.session.query(ArtefactVersion.version_number)
        .filter(ArtefactVersion.artefact_id == artefact_id)
        .order_by(ArtefactVersion.version_number.desc())
        .first()
    )
    return (last[0] + 1) if last else 1


def create_version(artefact, change_summary: str = "Edited") -> ArtefactVersion:
    """Snapshot the artefact's current state as a new immutable version.

    Commits. ``artefact`` must already be persisted (have an ``id``).
    """
    version = ArtefactVersion(
        artefact_id=artefact.id,
        version_number=next_version_number(artefact.id),
        kind=artefact.kind,
        title=artefact.title,
        content=artefact.content,
        status=artefact.status,
        priority=artefact.priority,
        change_summary=change_summary,
    )
    # A change to a source artefact makes its outgoing trace links suspect
    # (DOORS-style suspect-link semantics: dependants must be re-checked).
    db.session.query(ArtefactRelationship).filter(
        ArtefactRelationship.source_id == artefact.id
    ).update({"is_suspect": True}, synchronize_session=False)
    db.session.add(version)
    db.session.commit()
    return version


def latest_version(artefact) -> ArtefactVersion | None:
    """Return the most recent version of an artefact, or None."""
    return (
        db.session.query(ArtefactVersion)
        .filter(ArtefactVersion.artefact_id == artefact.id)
        .order_by(ArtefactVersion.version_number.desc())
        .first()
    )


def restore_version(artefact, version: ArtefactVersion) -> ArtefactVersion:
    """Promote ``version`` back onto the artefact; record a new version.

    Never rewrites history: the artefact moves to the restored content and a
    brand-new version number is created for that event.
    """
    artefact.kind = version.kind
    artefact.title = version.title
    artefact.content = version.content
    artefact.status = version.status
    artefact.priority = version.priority
    db.session.commit()  # AuditMixin stamps changed_on/changed_by here
    return create_version(artefact, f"Restored from version {version.version_number}")


# ---------------------------------------------------------------------------
# Baselines (FR-P4 — approved artefact versions only)
# ---------------------------------------------------------------------------
def snapshot_baseline(baseline) -> int:
    """Freeze the current APPROVED version of every artefact in the project.

    Baselines capture only artefacts whose latest version is in the ``Approved``
    state; draft / proposed / in-review items are skipped. An artefact without any
    prior version still gets an implicit snapshot version first, so a versioned
    reference is never required twice. Commits. Returns entry count.
    """
    baseline_entries = db.session.query(BaselineEntry).filter(
        BaselineEntry.baseline_id == baseline.id
    )
    if baseline_entries.count():
        # Idempotent guard (normally unreachable because baselines are immutable).
        return 0

    count = 0
    for artefact in baseline.project.artefacts:
        version = latest_version(artefact)
        if version is None:
            version = create_version(artefact, "Initial snapshot for baseline")
        if version.status != RequirementState.APPROVED:
            continue  # only currently-approved versions are baselined
        db.session.add(
            BaselineEntry(
                baseline_id=baseline.id,
                artefact_id=artefact.id,
                artefact_version_id=version.id,
            )
        )
        count += 1
    db.session.commit()
    return count


# --------------------------------------------------------------------------
# Display invariant (FR-P3 — an artefact shows its latest version's properties)
# --------------------------------------------------------------------------
def reconcile_artefact(artefact) -> bool:
    """Re-apply the latest Artefact Version's properties onto the live row.

    The latest version is the source of truth for what an artefact displays
    (docs/minimal_spec.md FR-P3). Normal writes keep the row mirrored via
    ``create_version``, so this only corrects drift caused by a direct write that
    bypassed versioning. Returns True if the row was corrected and committed.
    """
    version = latest_version(artefact)
    if version is None:
        return False
    changed = False
    for field in VERSIONED_FIELDS:
        if getattr(artefact, field) != getattr(version, field):
            setattr(artefact, field, getattr(version, field))
            changed = True
    if changed:
        db.session.commit()
    return changed


# ---------------------------------------------------------------------------
# Traceability (FR-5)
# ---------------------------------------------------------------------------
def _validate_kinds(rel_type: RelationshipType, source: Artefact, target: Artefact):
    """If the relationship type restricts artefact kinds, enforce them."""
    for spec, artefact in ((rel_type.source_kinds, source), (rel_type.target_kinds, target)):
        if not spec:
            continue
        try:
            allowed = json.loads(spec)
        except (TypeError, ValueError):
            continue  # malformed rule — ignore
        if allowed and artefact.kind and artefact.kind.name not in allowed:
            raise ValueError(
                f"Artefact kind '{artefact.kind.value}' is not allowed on this "
                f"end of a '{rel_type.display_name}' link."
            )


def validate_relationship(source: Artefact, target: Artefact, rel_type: RelationshipType):
    """Raise ValueError if the proposed link violates trace rules."""
    if source is None or target is None or rel_type is None:
        raise ValueError("Source, target and relationship type are required.")
    if source.project_id != target.project_id:
        raise ValueError("Relationship targets must belong to the same project.")
    if source.id == target.id:
        raise ValueError("An artefact cannot be linked to itself.")
    duplicate = (
        db.session.query(ArtefactRelationship)
        .filter_by(
            source_id=source.id,
            target_id=target.id,
            relationship_type_id=rel_type.id,
        )
        .first()
    )
    if duplicate:
        raise ValueError(
            f"A '{rel_type.display_name}' link already exists between these artefacts."
        )
    _validate_kinds(rel_type, source, target)


def create_relationship(source: Artefact, target: Artefact, rel_type: RelationshipType) -> ArtefactRelationship:
    """Validate and persist a trace link. Commits."""
    validate_relationship(source, target, rel_type)
    rel = ArtefactRelationship(
        project_id=source.project_id,
        source_id=source.id,
        target_id=target.id,
        relationship_type_id=rel_type.id,
        is_suspect=False,
    )
    db.session.add(rel)
    db.session.commit()
    return rel


def clear_suspect(rel: ArtefactRelationship) -> ArtefactRelationship:
    """Mark a link as reviewed/valid."""
    rel.is_suspect = False
    db.session.commit()
    return rel


# --- impact traversal -----------------------------------------------------
def _traverse(start_id: int, max_depth: int, direction: str) -> dict:
    """BFS over relationships returning {artefact_id: depth}.

    direction == "forward"  : follow outgoing links (source -> target)
    direction == "reverse"  : follow incoming links (target -> source)
    """
    impact = {start_id: 0}
    frontier = [start_id]
    depth = 1
    while frontier and depth <= max_depth:
        next_frontier = []
        if direction == "forward":
            rows = db.session.query(
                ArtefactRelationship.source_id, ArtefactRelationship.target_id
            ).filter(ArtefactRelationship.source_id.in_(frontier)).all()
            for _, target_id in rows:
                if target_id not in impact:
                    impact[target_id] = depth
                    next_frontier.append(target_id)
        else:
            rows = db.session.query(
                ArtefactRelationship.source_id, ArtefactRelationship.target_id
            ).filter(ArtefactRelationship.target_id.in_(frontier)).all()
            for source_id, _ in rows:
                if source_id not in impact:
                    impact[source_id] = depth
                    next_frontier.append(source_id)
        frontier = next_frontier
        depth += 1
    impact.pop(start_id, None)
    return impact


def impact_report(start_id: int, max_depth: int = 3) -> dict:
    """Impacted artefacts (both directions) plus suspect links in the set."""
    forward = _traverse(start_id, max_depth, "forward")
    reverse = _traverse(start_id, max_depth, "reverse")
    depths = {aid: min(fwd, rev) for aid, fwd, rev in (
        (i, forward.get(i, max_depth + 1), reverse.get(i, max_depth + 1))
        for i in set(forward) | set(reverse)
    )}
    ids = set(depths)
    artefacts = {
        a.id: a
        for a in db.session.query(Artefact).filter(Artefact.id.in_(ids)).all()
    }
    impacted = []
    for artefact_id, depth in sorted(depths.items(), key=lambda kv: (kv[1], kv[0])):
        impacted.append(
            {
                "artefact": artefacts[artefact_id],
                "depth": depth,
                "forward": artefact_id in forward,
                "reverse": artefact_id in reverse,
            }
        )
    suspects = (
        db.session.query(ArtefactRelationship)
        .filter(
            or_(
                ArtefactRelationship.source_id == start_id,
                ArtefactRelationship.target_id == start_id,
                ArtefactRelationship.source_id.in_(ids),
                ArtefactRelationship.target_id.in_(ids),
            ),
            ArtefactRelationship.is_suspect.is_(True),
        )
        .all()
    )
    return {"impacted": impacted, "suspects": suspects}


def graph_data(start_id: int, max_depth: int = 2) -> tuple[list, list]:
    """vis.js-compatible nodes/edges centred on ``start_id``."""
    forward = _traverse(start_id, max_depth, "forward")
    reverse = _traverse(start_id, max_depth, "reverse")
    depths = dict(forward)
    depths.update(reverse)
    depths.setdefault(start_id, 0)
    ids = set(depths)
    artefacts = {
        a.id: a
        for a in db.session.query(Artefact).filter(Artefact.id.in_(ids)).all()
    }
    nodes = [
        {
            "id": a.id,
            "label": a.title[:40],
            "kind": a.kind.name,
            "status": a.status.name,
            "level": depths[a.id],
        }
        for a in artefacts.values()
    ]
    rels = (
        db.session.query(ArtefactRelationship)
        .filter(
            or_(
                ArtefactRelationship.source_id.in_(ids),
                ArtefactRelationship.target_id.in_(ids),
            )
        )
        .all()
    )
    edges = [
        {
            "from": r.source_id,
            "to": r.target_id,
            "label": r.relationship_type.display_name,
            "suspect": bool(r.is_suspect),
        }
        for r in rels
    ]
    return nodes, edges


def orphan_artefacts(project_id: int) -> list[Artefact]:
    """Artefacts in a project with no trace links at all (inbound or outbound)."""
    related = set()
    for (rid,) in db.session.query(ArtefactRelationship.source_id).filter_by(
        project_id=project_id
    ):
        related.add(rid)
    for (rid,) in db.session.query(ArtefactRelationship.target_id).filter_by(
        project_id=project_id
    ):
        related.add(rid)
    if not related:
        return db.session.query(Artefact).filter_by(project_id=project_id).all()
    return (
        db.session.query(Artefact)
        .filter(
            Artefact.project_id == project_id, ~Artefact.id.in_(related)
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Review verdicts (FR-7 — DNG-style per-artefact decisions with progress)
# ---------------------------------------------------------------------------
def review_artefact_version(review, artefact) -> "ArtefactVersion | None":
    """The version a verdict/comment must pin for an artefact in a review.

    A review based on a baseline resolves to the **frozen baseline version** of
    the artefact (so decisions tie to exactly what was reviewed); otherwise it
    falls back to the artefact's latest version at decision time.
    """
    if review is not None and review.basis_baseline_id is not None:
        entry = (
            db.session.query(BaselineEntry)
            .filter_by(baseline_id=review.basis_baseline_id, artefact_id=artefact.id)
            .first()
        )
        if entry is not None:
            return entry.artefact_version
    return latest_version(artefact)


def review_scope(review) -> list:
    """The artefacts that are in scope of a review, in stable order.

    Baseline-based reviews cover the baseline snapshot entries; stream reviews
    cover every artefact in the project.
    """
    if review.basis_baseline_id is not None:
        entries = (
            db.session.query(BaselineEntry)
            .filter_by(baseline_id=review.basis_baseline_id)
            .order_by(BaselineEntry.artefact_id)
            .all()
        )
        return [e.artefact for e in entries]
    return (
        db.session.query(Artefact)
        .filter_by(project_id=review.project_id)
        .order_by(Artefact.id)
        .all()
    )


def validate_verdict_scope(review, artefact) -> None:
    """Raise ``ValueError`` if ``artefact`` cannot be reviewed in ``review``.

    Single source of truth for verdict scope, shared by the service
    (``record_verdict``) and the UI form hook (``ReviewVerdictView.pre_add``) so
    the two can never drift apart.
    """
    if review is None or artefact is None:
        raise ValueError("A verdict needs both a review and an artefact.")
    if artefact.project_id != review.project_id:
        raise ValueError(
            "That artefact belongs to a different project than the review - "
            "choose an artefact from the review's project."
        )
    if review.basis_baseline_id is not None:
        in_baseline = (
            db.session.query(BaselineEntry)
            .filter_by(baseline_id=review.basis_baseline_id, artefact_id=artefact.id)
            .count()
        )
        if not in_baseline:
            raise ValueError("This artefact is not part of the review's baseline scope.")


def record_verdict(
    review, reviewer, artefact, decision, comment: str = ""
) -> ReviewVerdict:
    """Create or update a reviewer's verdict on an artefact.

    The reviewed version is resolved and pinned automatically. One verdict per
    (review, reviewer, artefact); later calls replace the earlier decision.
    Commits. Raises ``ValueError`` when the artefact is outside the review.
    """
    if reviewer is None or decision is None:
        raise ValueError("reviewer and decision are required.")
    validate_verdict_scope(review, artefact)
    version = review_artefact_version(review, artefact)
    if version is None:
        version = create_version(artefact, "Snapshot before review verdict")
    verdict = (
        db.session.query(ReviewVerdict)
        .filter_by(
            review_id=review.id,
            reviewer_id=reviewer.id,
            artefact_id=artefact.id,
        )
        .first()
    )
    if verdict is None:
        verdict = ReviewVerdict(
            review_id=review.id,
            reviewer_id=reviewer.id,
            artefact_id=artefact.id,
            artefact_version_id=version.id,
            decision=decision,
            comment=comment or "",
        )
        db.session.add(verdict)
    else:
        verdict.decision = decision
        verdict.comment = comment or ""
        verdict.artefact_version_id = version.id
    db.session.commit()
    return verdict


def review_progress(review) -> dict:
    """Per-reviewer and overall decision progress over the review's scope."""
    scope = review_scope(review)
    total = len(scope)
    ids = [a.id for a in scope]
    reviewers = {}
    for assignment in (
        db.session.query(ReviewAssignment)
        .filter_by(review_id=review.id)
        .all()
    ):
        decided = 0
        if ids:
            decided = (
                db.session.query(ReviewVerdict)
                .filter(
                    ReviewVerdict.review_id == review.id,
                    ReviewVerdict.reviewer_id == assignment.reviewer.id,
                    ReviewVerdict.artefact_id.in_(ids),
                )
                .count()
            )
        percent = round(decided * 100 / total) if total else 0
        reviewers[assignment.reviewer.username] = {
            "decided": decided,
            "required": total,
            "percent": percent,
        }
    overall = (
        round(sum(v["percent"] for v in reviewers.values()) / len(reviewers))
        if reviewers
        else 0
    )
    return {
        "total": total,
        "reviewers": reviewers,
        "overall": overall,
        "reviewers_count": len(reviewers),
    }


def create_review_from_baseline(
    baseline,
    title: str | None = None,
    description: str | None = None,
    due_date=None,
    status=ReviewState.PLANNED,
) -> Review:
    """Create a review frozen over a baseline's snapshot (DNG pattern). Commits."""
    review = Review(
        project_id=baseline.project_id,
        basis_baseline_id=baseline.id,
        title=title or f"{baseline.name} Review",
        description=description or "Review of the baseline's approved content.",
        status=status,
        due_date=due_date,
    )
    db.session.add(review)
    db.session.commit()
    return review


# ---------------------------------------------------------------------------
# Workflow transition tables (FR-7 review, FR-8 change)
# ---------------------------------------------------------------------------
REVIEW_TRANSITIONS = {
    ReviewState.PLANNED: {ReviewState.ACTIVE},
    ReviewState.ACTIVE: {ReviewState.COMMENT_RESOLUTION, ReviewState.APPROVED},
    ReviewState.COMMENT_RESOLUTION: {ReviewState.APPROVED},
    ReviewState.APPROVED: {ReviewState.CLOSED},
    ReviewState.CLOSED: set(),
}

CHANGE_TRANSITIONS = {
    ChangeState.SUBMITTED: {ChangeState.ANALYSED},
    ChangeState.ANALYSED: {ChangeState.APPROVED},
    ChangeState.APPROVED: {ChangeState.IMPLEMENTED},
    ChangeState.IMPLEMENTED: {ChangeState.VERIFIED},
    ChangeState.VERIFIED: {ChangeState.CLOSED},
    ChangeState.CLOSED: set(),
}


def can_transition(current, target) -> bool:
    """Translate an enum value into its member and consult the correct table."""
    from app.models import ChangeRequest, Review

    # Caller passes the enum value (e.g. ReviewState.ACTIVE); normalise.
    if isinstance(current, ReviewState):
        return target in REVIEW_TRANSITIONS.get(current, set())
    if isinstance(current, ChangeState):
        return target in CHANGE_TRANSITIONS.get(current, set())
    # Unknown workflow object
    return False


# ---------------------------------------------------------------------------
# The path each "Next step" button follows (FR-P7, FR-P8)
# ---------------------------------------------------------------------------
# The transition tables above say what is *legal*; these say what the single
# advance button *does* when a state has more than one legal successor. Every
# entry must also be legal in the table - pinned by tests.
REVIEW_ADVANCE = {
    ReviewState.PLANNED: ReviewState.ACTIVE,
    ReviewState.ACTIVE: ReviewState.APPROVED,
    ReviewState.COMMENT_RESOLUTION: ReviewState.APPROVED,
    ReviewState.APPROVED: ReviewState.CLOSED,
    ReviewState.CLOSED: None,
}

CHANGE_ADVANCE = {
    ChangeState.SUBMITTED: ChangeState.ANALYSED,
    ChangeState.ANALYSED: ChangeState.APPROVED,
    ChangeState.APPROVED: ChangeState.IMPLEMENTED,
    ChangeState.IMPLEMENTED: ChangeState.VERIFIED,
    ChangeState.VERIFIED: ChangeState.CLOSED,
    ChangeState.CLOSED: None,
}

# The branch 'Active' may also take: send it back for comment resolution.
REVIEW_REQUEST_CHANGES = {ReviewState.ACTIVE: ReviewState.COMMENT_RESOLUTION}

# A review is open for verdicts and signatures while it is in one of these.
REVIEW_DECIDABLE_FROM = {ReviewState.ACTIVE, ReviewState.COMMENT_RESOLUTION}


def next_review_state(current):
    """The state a review's "Next step" button moves to, or None when closed."""
    return REVIEW_ADVANCE.get(current)


def next_change_state(current):
    """The state a change request's "Next step" button moves to, or None."""
    return CHANGE_ADVANCE.get(current)


def request_changes_state(current):
    """The 'send back for comment' target, or None when not applicable."""
    return REVIEW_REQUEST_CHANGES.get(current)


def record_approval(review, reviewer, decision, comment: str = "") -> Approval:
    """Sign a review on behalf of ``reviewer`` (one current position each).

    Re-signing replaces that reviewer's earlier decision (the audit columns still
    show that it changed), so a double click cannot stack up duplicate signatures.
    Commits.
    """
    if review is None or reviewer is None or decision is None:
        raise ValueError("review, reviewer and decision are required.")
    approval = (
        db.session.query(Approval)
        .filter_by(review_id=review.id, reviewer_id=reviewer.id)
        .order_by(Approval.id.desc())
        .first()
    )
    if approval is None:
        approval = Approval(
            review_id=review.id,
            reviewer_id=reviewer.id,
            decision=decision,
            comment=comment or "",
        )
        db.session.add(approval)
    else:
        approval.decision = decision
        approval.comment = comment or ""
    db.session.commit()
    return approval


def baselines_holding(artefact) -> list:
    """Baselines that freeze this artefact (empty when it is free to delete)."""
    return (
        db.session.query(Baseline)
        .join(BaselineEntry, BaselineEntry.baseline_id == Baseline.id)
        .filter(BaselineEntry.artefact_id == artefact.id)
        .all()
    )


def trace_links_holding(artefact) -> int:
    """How many trace links pin this artefact at either end."""
    return (
        db.session.query(ArtefactRelationship)
        .filter(
            (ArtefactRelationship.source_id == artefact.id)
            | (ArtefactRelationship.target_id == artefact.id)
        )
        .count()
    )


def baseline_skipped(baseline) -> list:
    """Artefacts a baseline could not capture: latest version not Approved."""
    result = []
    for artefact in baseline.project.artefacts:
        version = latest_version(artefact)
        if version is None or version.status != RequirementState.APPROVED:
            result.append(artefact)
    return result


def default_baseline_name(project) -> str:
    """The dated name both baseline doors use, e.g. 'HX4 BL 2026-09-22'."""
    return f"{project.project_code} BL {date.today().isoformat()}"