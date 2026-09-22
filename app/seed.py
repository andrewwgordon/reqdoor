"""Demonstration dataset generator for the HoverX-4 quadcopter UAV system.

Seeds a realistic mid-sized dataset that exercises every Phase 2 feature through
the user interface:

* Projects / Artefacts (all kinds) + immutable versions + a restore
* Relationship types + validated trace links + suspect links
* Traceability graph feed, impact analysis, orphan artefacts
* A baseline frozen over an earlier artefact version
* Reviews (assignments, threaded comments, approvals) in different states
* Change requests (items, applied flags, different workflow states)
* Demo users to sign reviews / own change requests

Usage (uses the configured database, by default ``app.db``):

    uv run python -m app.seed create     # idempotent — no-op if HX4 exists
    uv run python -m app.seed destroy    # removes everything this seed created

The seed is fully reversible: ``destroy`` removes only rows that belong to the
seeded HX4 project (plus the demo users and relationship types it created), so
other data in the database is left untouched.
"""

import sys
from datetime import date

from flask import g

from app import create_app
from app.extensions import appbuilder, db
from app.models import (
    Approval,
    Artefact,
    ArtefactKind,
    ArtefactRelationship,
    ArtefactVersion,
    Baseline,
    BaselineEntry,
    ChangeAction,
    ChangeRequest,
    ChangeSetItem,
    ChangeState,
    Comment,
    Decision,
    Priority,
    Project,
    RelationshipType,
    RequirementState,
    Review,
    ReviewAssignment,
    ReviewState,
    ReviewVerdict,
)
from app.services import (
    create_relationship,
    create_version,
    record_verdict,
    restore_version,
    review_artefact_version,
    snapshot_baseline,
)

SEED_PROJECT_CODE = "HX4"
SEED_PROJECT_NAME = "HoverX-4 Quadcopter UAV"

RELATIONSHIP_TYPES = [
    # (name, display_name, directional, source_kinds, target_kinds)
    ("SATISFIES", "Satisfies", True, None, None),
    ("REFINES", "Refines", True, None, None),
    ("ELABORATES", "Elaborates", True, None, None),
    ("DEPENDS_ON", "Depends On", True, None, None),
    ("IMPLEMENTS", "Implements", True, '["SOFTWARE_REQ"]', '["SYSTEM_REQ"]'),
    ("VERIFIES", "Verifies", True, '["TEST_CASE"]', '["SYSTEM_REQ","SOFTWARE_REQ","USE_CASE"]'),
    ("MITIGATES", "Mitigates", True, '["SYSTEM_REQ"]', '["RISK"]'),
    ("DERIVES_FROM", "Derives From", True, None, None),
    ("REFERENCES", "References", True, None, None),
    ("CONFLICTS_WITH", "Conflicts With", False, None, None),
]

DEMO_USERNAMES = ["admin", "jane.doe", "marc.lee"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user(username, password, first_name, last_name):
    user = appbuilder.sm.find_user(username=username)
    if user is None:
        role = appbuilder.sm.find_role(appbuilder.sm.auth_role_admin)
        user = appbuilder.sm.add_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=f"{username}@example.com",
            role=role,
            password=password,
        )
    return user


def _artefact(project, kind, title, content="", status=RequirementState.DRAFT,
              priority=Priority.MEDIUM):
    a = Artefact(
        project_id=project.id,
        kind=kind,
        title=title,
        content=content,
        status=status,
        priority=priority,
    )
    db.session.add(a)
    db.session.commit()
    create_version(a, "Initial draft")
    return a


def _edit(artefact, summary, **fields):
    for key, value in fields.items():
        setattr(artefact, key, value)
    db.session.commit()
    create_version(artefact, summary)


def _link(source, target, rel_type):
    return create_relationship(source, target, rel_type)


def _baseline(project, name, description):
    baseline = Baseline(project_id=project.id, name=name, description=description)
    db.session.add(baseline)
    db.session.commit()
    snapshot_baseline(baseline)
    return baseline


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
def seed():
    existing = db.session.query(Project).filter_by(project_code=SEED_PROJECT_CODE).first()
    if existing is not None:
        print(f"[seed] {SEED_PROJECT_CODE} already seeded — no-op. "
              f"Run 'python -m app.seed destroy' to reset it.")
        return

    # Demo users (admin is guaranteed so audit columns can be stamped).
    admin = _user("admin", "admin", "System", "Admin")
    jane = _user("jane.doe", "password", "Jane", "Doe")
    marc = _user("marc.lee", "password", "Marc", "Lee")
    g.user = admin

    # ---- Project ----------------------------------------------------------
    project = Project(
        project_code=SEED_PROJECT_CODE,
        name=SEED_PROJECT_NAME,
        description=(
            "Small quadcopter UAV for inspection and light-delivery missions. "
            "Seed dataset exercising Phase 2 traceability, reviews and change "
            "management in the user interface."
        ),
    )
    db.session.add(project)
    db.session.commit()

    # ---- Relationship types ------------------------------------------------
    types = {}
    for name, display, directional, source_kinds, target_kinds in RELATIONSHIP_TYPES:
        rt = RelationshipType(
            name=name,
            display_name=display,
            is_directional=directional,
            source_kinds=source_kinds,
            target_kinds=target_kinds,
        )
        db.session.add(rt)
        db.session.commit()
        types[name] = rt
    sat, satisfies = "SATISFIES", types["SATISFIES"]
    refin, refines = "REFINES", types["REFINES"]
    elabor, elaborates = "ELABORATES", types["ELABORATES"]
    deps = types["DEPENDS_ON"]
    impl = types["IMPLEMENTS"]
    verif = types["VERIFIES"]
    mitig = types["MITIGATES"]
    refs = types["REFERENCES"]

    # ---- Stakeholder requirements -----------------------------------------
    sr_001 = _artefact(project, ArtefactKind.STAKEHOLDER_REQ,
                       "SR-001 Safe operation BVLOS",
                       "The UAV shall support safe flight beyond visual line of sight.",
                       status=RequirementState.APPROVED, priority=Priority.HIGH)
    sr_002 = _artefact(project, ArtefactKind.STAKEHOLDER_REQ,
                       "SR-002 Payload of at least 1 kg",
                       "The UAV shall carry a 1 kg inspection camera payload.",
                       status=RequirementState.APPROVED, priority=Priority.MEDIUM)
    sr_003 = _artefact(project, ArtefactKind.STAKEHOLDER_REQ,
                       "SR-003 Endurance of at least 30 minutes",
                       "The UAV shall remain airborne for at least 30 minutes of hover.",
                       status=RequirementState.APPROVED, priority=Priority.HIGH)
    sr_004 = _artefact(project, ArtefactKind.STAKEHOLDER_REQ,
                       "SR-004 Autonomous return-to-home",
                       "The UAV shall automatically return to home on low battery.",
                       status=RequirementState.RELEASED, priority=Priority.CRITICAL)
    sr_005 = _artefact(project, ArtefactKind.STAKEHOLDER_REQ,
                       "SR-005 Handheld controller, 5 km range",
                       "The operator shall control the UAV from a handheld console at up to 5 km.",
                       status=RequirementState.PROPOSED, priority=Priority.MEDIUM)

    # ---- System requirements ------------------------------------------------
    sys_001 = _artefact(project, ArtefactKind.SYSTEM_REQ,
                        "SYS-001 Wind stability to 12 m/s",
                        "The vehicle shall maintain attitude stability in winds up to 12 m/s.",
                        status=RequirementState.PROPOSED, priority=Priority.HIGH)
    sys_002 = _artefact(project, ArtefactKind.SYSTEM_REQ,
                        "SYS-002 Power for 30 minutes hover",
                        "The power system shall sustain 30 minutes of hover.",
                        status=RequirementState.PROPOSED, priority=Priority.HIGH)
    sys_003 = _artefact(project, ArtefactKind.SYSTEM_REQ,
                        "SYS-003 Redundant flight controller",
                        "The vehicle shall continue controlled flight after a primary "
                        "flight-controller failure.",
                        status=RequirementState.DRAFT, priority=Priority.CRITICAL)
    sys_004 = _artefact(project, ArtefactKind.SYSTEM_REQ,
                        "SYS-004 Autonomous return-to-home",
                        "The vehicle shall return to the home point within 2 m accuracy.",
                        status=RequirementState.PROPOSED, priority=Priority.CRITICAL)
    sys_005 = _artefact(project, ArtefactKind.SYSTEM_REQ,
                        "SYS-005 Weight budget 2 kg",
                        "Total take-off weight shall not exceed 2 kg including payload.",
                        status=RequirementState.APPROVED, priority=Priority.MEDIUM)

    # ---- Software requirements ---------------------------------------------
    sw_001 = _artefact(project, ArtefactKind.SOFTWARE_REQ,
                       "SW-001 400 Hz attitude loop",
                       "The flight stack shall run the attitude loop at 400 Hz.",
                       status=RequirementState.PROPOSED, priority=Priority.HIGH)
    sw_002 = _artefact(project, ArtefactKind.SOFTWARE_REQ,
                       "SW-002 Low-battery alert at 20 % SOC",
                       "The battery monitor shall alert at 20 % state of charge.",
                       status=RequirementState.PROPOSED, priority=Priority.HIGH)
    sw_003 = _artefact(project, ArtefactKind.SOFTWARE_REQ,
                       "SW-003 Telemetry within 500 ms",
                       "The ground-control app shall show telemetry within 500 ms.",
                       status=RequirementState.PROPOSED, priority=Priority.MEDIUM)
    sw_004 = _artefact(project, ArtefactKind.SOFTWARE_REQ,
                       "SW-004 RTH guidance to 2 m",
                       "The RTH routine shall guide the vehicle to home within 2 m.",
                       status=RequirementState.PROPOSED, priority=Priority.CRITICAL)

    # ---- Use cases, features, user stories, risks, tests, defects -----------
    uc_002 = _artefact(project, ArtefactKind.USE_CASE,
                       "UC-002 Autonomous return on low battery",
                       "Actor: UAV manager. The vehicle detects low battery, enters "
                       "auto-return and lands at the home point.",
                       status=RequirementState.PROPOSED, priority=Priority.CRITICAL)
    f_001 = _artefact(project, ArtefactKind.FEATURE,
                      "F-001 Autonomous flight-modes package",
                      "Attitude hold, orbit, waypoint and RTH flight modes.")
    us_001 = _artefact(project, ArtefactKind.USER_STORY,
                       "US-001 Fail-safe return-to-home",
                       "As a ground operator, I want a fail-safe RTH so that I avoid flyaways.",
                       status=RequirementState.APPROVED, priority=Priority.CRITICAL)
    us_002 = _artefact(project, ArtefactKind.USER_STORY,
                       "US-002 Live battery telemetry",
                       "As a pilot, I want live battery telemetry so that I can land safely.",
                       status=RequirementState.PROPOSED, priority=Priority.MEDIUM)
    rsk_002 = _artefact(project, ArtefactKind.RISK,
                        "RSK-002 Battery thermal runaway",
                        "A cell failure may propagate to thermal runaway in flight.",
                        status=RequirementState.APPROVED, priority=Priority.CRITICAL)
    rsk_003 = _artefact(project, ArtefactKind.RISK,
                        "RSK-003 Loss of RC link / flyaway",
                        "Loss of the RC link may leave the vehicle uncontrolled.",
                        status=RequirementState.APPROVED, priority=Priority.CRITICAL)
    tc_001 = _artefact(project, ArtefactKind.TEST_CASE,
                       "TC-001 Hover stability in 12 m/s wind",
                       "Execute 10-minute hover in a 12 m/s wind and verify attitude < 5 deg.",
                       status=RequirementState.APPROVED, priority=Priority.HIGH)
    tc_002 = _artefact(project, ArtefactKind.TEST_CASE,
                       "TC-002 30-minute hover endurance",
                       "Hover with a 1 kg payload until battery low; verify 30 minutes.",
                       status=RequirementState.APPROVED, priority=Priority.HIGH)
    tc_003 = _artefact(project, ArtefactKind.TEST_CASE,
                       "TC-003 Low-battery RTH completion",
                       "Drain battery to 20 % SOC; verify automatic return and 2 m landing.",
                       status=RequirementState.PROPOSED, priority=Priority.CRITICAL)
    tc_004 = _artefact(project, ArtefactKind.TEST_CASE,
                       "TC-004 Telemetry latency under 500 ms",
                       "Measure end-to-end telemetry latency over 5 km link; expect < 500 ms.",
                       status=RequirementState.PROPOSED, priority=Priority.MEDIUM)
    d_001 = _artefact(project, ArtefactKind.DEFECT,
                      "D-001 GCS telemetry stutters under load",
                      "Telemetry frames drop when the ground station is under heavy load.",
                      status=RequirementState.PROPOSED, priority=Priority.MEDIUM)
    # Orphan artefacts — deliberately not linked, to surface in the orphan report
    f_002 = _artefact(project, ArtefactKind.FEATURE,
                      "F-002 Quick-release payload platform",
                      "Tool-free payload swap platform (not yet traced).")
    d_002 = _artefact(project, ArtefactKind.DEFECT,
                      "D-002 Intermittent compass calibration drift",
                      "Occasional calibration drift reported in field trials (not yet traced).")

    # ---- Trace links --------------------------------------------------------
    _link(sys_001, sr_001, satisfies)        # system satisfies stakeholder
    _link(sys_002, sr_003, satisfies)
    _link(sys_004, sr_004, satisfies)
    _link(sys_005, sr_002, satisfies)
    _link(sys_003, sr_001, refines)          # redundant controller refines BVLOS safety
    _link(sw_001, sys_001, impl)             # software implements system
    _link(sw_002, sys_002, impl)
    _link(sw_004, sys_004, impl)
    _link(sw_003, sr_005, elaborates)        # GCS telemetry elaborates the 5 km range
    _link(uc_002, sys_004, elaborates)       # use case elaborates RTH
    _link(f_001, sys_004, elaborates)
    _link(us_001, sys_004, refines)          # user story refines RTH
    _link(us_002, sw_003, refines)
    _link(sw_002, sw_001, deps)              # software dependencies
    _link(sw_004, sw_001, deps)
    _link(tc_001, sys_001, verif)            # tests verify
    _link(tc_002, sys_002, verif)
    _link(tc_003, sys_004, verif)
    _link(tc_004, sw_003, verif)
    _link(sys_002, rsk_002, mitig)           # power design mitigates battery risk
    _link(sys_004, rsk_003, mitig)           # RTH mitigates flyaway
    _link(d_001, sw_003, refs)               # defect references the software requirement

    # ---- Baseline over the FIRST generation (approved versions only) ---------
    # FR-P4: baselines capture only currently-approved artefact versions, so this
    # snapshots the 9 v1 artefacts whose status is Approved (SR-001/2/3, SYS-005,
    # US-001, RSK-002/3, TC-001/2). Draft/Proposed/Released items are excluded.
    baseline_01 = _baseline(
        project,
        "HX4 Design Review BL-0.1",
        "Initial approved system/software requirements snapshot for the design review.",
    )

    # ---- Second generation edits (creates suspect links + version drift) ----
    _edit(sr_001, "Edited", content=(
        "The UAV shall support safe flight beyond visual line of sight, including "
        "fail-safe behaviour on loss of the control link."))
    _edit(sys_002, "Edited", content=(
        "The power system shall sustain 30 minutes of hover at 1 kg payload."))
    _edit(tc_004, "Edited", content=(
        "Measure end-to-end telemetry latency over the 5 km link under maximum "
        "ground-station load; expect < 500 ms."))
    _edit(sw_002, "Edited", priority=Priority.HIGH)
    # Restore an artefact to its version 1 (records a new "restored" version)
    restore_version(sr_003, sr_003.versions[0])

    # ---- Reviews -------------------------------------------------------------
    review_01 = Review(
        project_id=project.id,
        title="Stakeholder & Safety Review — Round 1",
        description="Review SR and RSK artefacts for the requirement freeze.",
        # Frozen over the design-review baseline (DNG review-from-baseline:
        # verdicts/comments resolve to the baseline versions, not live rows).
        basis_baseline_id=baseline_01.id,
        status=ReviewState.ACTIVE,
        due_date=date(2026, 10, 30),
    )
    db.session.add(review_01)
    db.session.commit()
    db.session.add_all([
        ReviewAssignment(review_id=review_01.id, reviewer=jane, role_in_review="Chair",
                         response_status="Open"),
        ReviewAssignment(review_id=review_01.id, reviewer=marc, role_in_review="Reviewer",
                         response_status="Open"),
    ])
    db.session.commit()
    # Threaded comments on the review
    c1 = Comment(review_id=review_01.id, body=(
        "SR-001: can we tighten the fail-safe requirement to a worst-case "
        "single-failure analysis?"))
    db.session.add(c1)
    db.session.commit()
    db.session.add(
        Comment(review_id=review_01.id, parent_id=c1.id,
                body="Agreed — flagging for the safety work package."))
    # Comment on SR-001 under review — pinned to the baseline (frozen v1) version.
    db.session.add(
        Comment(review_id=review_01.id, artefact_id=sr_001.id,
                artefact_version_id=review_artefact_version(review_01, sr_001).id,
                body=(
                    "SR-001 wording updated in v2; please re-review the fail-safe clause."
                ),
                resolved=False))
    db.session.commit()
    db.session.add(
        Approval(review_id=review_01.id, reviewer=admin, decision=Decision.APPROVE,
                 comment="System requirements look sound; software items still open."))
    db.session.commit()

    # DNG-style per-artefact verdicts — decisions tie to the baseline versions,
    # and per-reviewer progress is computed over the 9 baseline artefacts.
    record_verdict(review_01, jane, sr_001, Decision.APPROVE,
                   "Wording now covers the fail-safe analysis request.")
    record_verdict(review_01, jane, sr_003, Decision.APPROVE)
    record_verdict(review_01, jane, sys_005, Decision.ABSTAIN)
    record_verdict(review_01, jane, tc_001, Decision.APPROVE)
    record_verdict(review_01, marc, sr_001, Decision.APPROVE)
    record_verdict(review_01, marc, sr_002, Decision.APPROVE)
    record_verdict(review_01, marc, rsk_003, Decision.REJECT,
                   "Flyaway mitigation needs a link to the redundant controller.")
    record_verdict(review_01, marc, tc_002, Decision.APPROVE)

    review_02 = Review(
        project_id=project.id,
        title="Software Verification Review",
        description="Verify SW requirements and TC linkage before baselining.",
        status=ReviewState.PLANNED,
        due_date=date(2026, 11, 15),
    )
    db.session.add(review_02)
    db.session.commit()
    db.session.add(
        ReviewAssignment(review_id=review_02.id, reviewer=marc, role_in_review="Reviewer"))
    db.session.commit()

    # ---- Change requests ------------------------------------------------------
    cr_001 = ChangeRequest(
        project_id=project.id, title="Camera gimbal payload interface",
        description="Add a gimbal payload interface and relax the weight budget.",
        status=ChangeState.CLOSED, priority=Priority.MEDIUM, requested_by=jane)
    db.session.add(cr_001)
    db.session.commit()
    db.session.add(
        ChangeSetItem(change_request_id=cr_001.id, artefact_id=sys_005.id,
                      action=ChangeAction.UPDATE,
                      proposed_change="Revise weight budget to 2.25 kg incl. gimbal.",
                      applied=True))
    db.session.commit()

    cr_002 = ChangeRequest(
        project_id=project.id, title="Improve RTH accuracy to 0.5 m",
        description="Hardening request from TC-003: tighten RTH landing accuracy.",
        status=ChangeState.APPROVED, priority=Priority.HIGH, requested_by=marc)
    db.session.add(cr_002)
    db.session.commit()
    db.session.add_all([
        ChangeSetItem(change_request_id=cr_002.id, artefact_id=sw_004.id,
                      action=ChangeAction.UPDATE,
                      proposed_change="Guidance to 0.5 m using barometric fusion.",
                      applied=False),
        ChangeSetItem(change_request_id=cr_002.id, artefact_id=tc_003.id,
                      action=ChangeAction.UPDATE,
                      proposed_change="Assert 0.5 m landing accuracy in the test case.",
                      applied=False),
    ])
    db.session.commit()

    cr_003 = ChangeRequest(
        project_id=project.id, title="Fix telemetry stutter (D-001)",
        description="Prioritise telemetry frames over diagnostics under load.",
        status=ChangeState.SUBMITTED, priority=Priority.MEDIUM, requested_by=jane)
    db.session.add(cr_003)
    db.session.commit()
    db.session.add(
        ChangeSetItem(change_request_id=cr_003.id, artefact_id=sw_003.id,
                      action=ChangeAction.UPDATE,
                      proposed_change="Add frame-priority scheduler; retest TC-004.",
                      applied=False))
    db.session.commit()

    # ---- Artefact-level comments (shown on the artefact detail page) ----------
    ac1 = Comment(artefact_id=sw_003.id, body=(
        "Suspect after v2 edit — re-verify against TC-004."), resolved=True)
    db.session.add(ac1)
    db.session.commit()
    db.session.add(
        Comment(artefact_id=sw_003.id, parent_id=ac1.id,
                body="Verified in bench test; resolving."))
    db.session.commit()

    print(f"[seed] Seeded {SEED_PROJECT_NAME} ({SEED_PROJECT_CODE})")
    artefact_count = (
        db.session.query(Artefact).filter_by(project_id=project.id).count())
    version_count = (
        db.session.query(ArtefactVersion)
        .join(Artefact)
        .filter(Artefact.project_id == project.id)
        .count())
    relationship_count = (
        db.session.query(ArtefactRelationship)
        .filter_by(project_id=project.id).count())
    baseline_entry_count = (
        db.session.query(BaselineEntry)
        .filter_by(baseline_id=baseline_01.id).count())
    review_count = db.session.query(Review).filter_by(project_id=project.id).count()
    approval_count = (
        db.session.query(Approval)
        .join(Review)
        .filter(Review.project_id == project.id)
        .count()
    )
    verdict_count = (
        db.session.query(ReviewVerdict)
        .join(Review)
        .filter(Review.project_id == project.id)
        .count()
    )
    cr_count = db.session.query(ChangeRequest).filter_by(project_id=project.id).count()
    print(f"[seed]   artefacts         : {artefact_count}")
    print(f"[seed]   versions          : {version_count}")
    print(f"[seed]   relationships     : {relationship_count}")
    print(f"[seed]   baseline entries  : {baseline_entry_count}")
    print(f"[seed]   reviews/approvals/verdicts : {review_count}/{approval_count}/{verdict_count}")
    print(f"[seed]   change requests   : {cr_count}")


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
def teardown():
    project = db.session.query(Project).filter_by(project_code=SEED_PROJECT_CODE).first()
    if project is None:
        print("[seed] Nothing to tear down — HX4 project not present.")
        return

    project_id = project.id
    artefact_ids = [row[0] for row in db.session.query(Artefact.id).filter_by(project_id=project_id)]
    review_ids = [row[0] for row in db.session.query(Review.id).filter_by(project_id=project_id)]
    cr_ids = [row[0] for row in db.session.query(ChangeRequest.id).filter_by(project_id=project_id)]

    # Child-first so FK ORDERs never block the delete in any engine.
    db.session.query(BaselineEntry).filter(BaselineEntry.baseline_id.in_(
        db.session.query(Baseline.id).filter_by(project_id=project_id))).delete(synchronize_session=False)
    db.session.query(Baseline).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.session.query(Approval).filter(Approval.review_id.in_(review_ids)).delete(synchronize_session=False)
    db.session.query(Comment).filter(
        (Comment.review_id.in_(review_ids)) | (Comment.artefact_id.in_(artefact_ids))
    ).delete(synchronize_session=False)
    db.session.query(ReviewVerdict).filter(ReviewVerdict.review_id.in_(review_ids)).delete(synchronize_session=False)
    db.session.query(ReviewAssignment).filter(ReviewAssignment.review_id.in_(review_ids)).delete(synchronize_session=False)
    db.session.query(Review).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.session.query(ChangeSetItem).filter(ChangeSetItem.change_request_id.in_(cr_ids)).delete(synchronize_session=False)
    db.session.query(ChangeRequest).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.session.query(ArtefactRelationship).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.session.query(ArtefactVersion).filter(ArtefactVersion.artefact_id.in_(artefact_ids)).delete(synchronize_session=False)
    db.session.query(Artefact).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.session.query(Project).filter_by(id=project_id).delete(synchronize_session=False)
    db.session.commit()

    # Remove the relationship types the seed created, but only if no other
    # project still uses them.
    for name, _display, _directional, _sk, _tk in RELATIONSHIP_TYPES:
        rt = db.session.query(RelationshipType).filter_by(name=name).first()
        if rt is not None:
            used = (db.session.query(ArtefactRelationship)
                    .filter_by(relationship_type_id=rt.id).count())
            if not used:
                db.session.delete(rt)
    db.session.commit()

    # Remove the demo users created by this seed. Only delete a user when the
    # username/email pair matches the seed's own signature (`{name}@example.com`)
    # so a pre-existing user (e.g. a real admin) is never removed.
    for username in DEMO_USERNAMES:
        user = appbuilder.sm.find_user(username=username)
        if user is not None:
            if user.email != f"{username}@example.com":
                print(f"[seed] Keeping '{username}' — pre-existing user, not seed-created.")
                continue
            db.session.delete(user)
    db.session.commit()

    print(f"[seed] Removed {SEED_PROJECT_NAME} and all seed-created data.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    app = create_app()
    with app.app_context():
        db.create_all()
        if len(argv) != 1 or argv[0] not in ("create", "destroy"):
            print(__doc__)
            return 2
        if argv[0] == "create":
            seed()
        else:
            teardown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())