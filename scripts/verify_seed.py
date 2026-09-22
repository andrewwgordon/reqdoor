"""Standalone verification of the HX4 seed dataset — run against the configured DB.

    uv run python -m scripts.verify_seed

Exits non-zero (with a report) if any invariant is violated.
"""

import sys

from app import create_app
from app.extensions import db
from app.models import (
    Approval,
    Artefact,
    ArtefactRelationship,
    ArtefactVersion,
    Baseline,
    BaselineEntry,
    ChangeRequest,
    Comment,
    Project,
    RelationshipType,
    Review,
    ReviewAssignment,
    ReviewVerdict,
)

FAILURES = []


def check(label, ok, detail=""):
    status = "ok  " if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def main():
    app = create_app()
    with app.app_context():
        project = db.session.query(Project).filter_by(project_code="HX4").first()
        if project is None:
            print("[FAIL] HX4 project not seeded.")
            return 1

        pid = project.id

        # ---- counts -------------------------------------------------------
        # 5 SR + 5 SYS + 4 SW + UC-002 + F-001 + 2 US + 2 RSK + 4 TC + D-001 + F-002 + D-002
        n_art = db.session.query(Artefact).filter_by(project_id=pid).count()
        check("artefact count", n_art == 27, f"{n_art} != 27")

        n_rel = db.session.query(ArtefactRelationship).filter_by(project_id=pid).count()
        check("relationship count", n_rel == 22, f"{n_rel} != 22")

        # kinds covered
        from app.models import ArtefactKind
        kinds = {k for (k,) in db.session.query(Artefact.kind).filter_by(project_id=pid)}
        check("all 9 artefact kinds present",
              kinds == set(ArtefactKind), f"missing {set(ArtefactKind) - kinds}")

        # ---- versions -----------------------------------------------------
        n_ver = (
            db.session.query(ArtefactVersion)
            .join(Artefact)
            .filter(Artefact.project_id == pid)
            .count()
        )
        # 27 artefacts, 4 first-generation edits, 1 restore => 27 + 5
        check("version count", n_ver == 32, f"{n_ver} != 32")

        # latest version must always mirror the live artefact
        drift = []
        for v in db.session.query(ArtefactVersion).join(Artefact).filter(
                Artefact.project_id == pid).all():
            art = v.artefact
            if art is None:
                continue
            latest = (
                db.session.query(ArtefactVersion)
                .filter_by(artefact_id=art.id)
                .order_by(ArtefactVersion.version_number.desc())
                .first()
            )
            if latest.id == v.id and (
                v.title != art.title or v.content != art.content
                or v.kind != art.kind or v.status != art.status
                or v.priority != art.priority
            ):
                drift.append(f"{art.title} v{v.version_number}")
        check("latest version mirrors artefact", not drift, ", ".join(drift))

        # version numbers contiguous 1..n per artefact
        bad_gap = []
        for art in db.session.query(Artefact).filter_by(project_id=pid).all():
            nums = [n for (n,) in db.session.query(ArtefactVersion.version_number)
                    .filter_by(artefact_id=art.id).order_by(ArtefactVersion.version_number)]
            if nums != list(range(1, len(nums) + 1)):
                bad_gap.append(f"{art.title}: {nums}")
        check("version numbers contiguous", not bad_gap, ", ".join(bad_gap))

        # sr_003 restored (never edited): v1 initial + v2 "Restored from version 1"
        sr003 = db.session.query(Artefact).filter_by(project_id=pid,
                                                     title="SR-003 Endurance of at least 30 minutes").first()
        if sr003:
            sums = [v.change_summary for v in sorted(sr003.versions, key=lambda x: x.version_number)]
            check("SR-003 restore recorded v2", len(sums) == 2 and sums[-1] == "Restored from version 1",
                  str(sums))

        # ---- suspect links ------------------------------------------------
        # edits to sr_001 / sys_002 / tc_004 / sw_002 & restore of sr_003 make
        # their OUTGOING links suspect (5 links in total + sw_002 has none out).
        suspects = (
            db.session.query(ArtefactRelationship)
            .filter_by(project_id=pid, is_suspect=True)
            .order_by(ArtefactRelationship.source_id)
            .all()
        )
        check("suspect links == 5", len(suspects) == 5, f"{len(suspects)} != 5")
        for s in suspects:
            print(f"        suspect: {s.source.title[:30]:32s} "
                  f"{s.relationship_type.display_name:14s} {s.target.title[:30]}")

        # ---- baseline ------------------------------------------------------
        # FR-P4: only currently-approved versions are baselined.
        from app.models import RequirementState
        approved_1g = [
            t for (t,) in db.session.query(Artefact.title).filter_by(
                project_id=pid, status=RequirementState.APPROVED)
        ]
        check("9 approved artefacts in seed", len(approved_1g) == 9,
              f"{len(approved_1g)} != 9: {approved_1g}")

        bl = db.session.query(Baseline).filter_by(project_id=pid).first()
        check("baseline exists", bl is not None)
        if bl:
            entries = db.session.query(BaselineEntry).filter_by(baseline_id=bl.id).all()
            check("baseline covers approved artefacts only (9 entries)",
                  len(entries) == 9, f"{len(entries)} != 9")
            # entries must be pinned to the first generation (v1)
            bad = [e for e in entries if e.artefact_version.version_number != 1]
            check("baseline pinned to first generation (v1)",
                  not bad, ", ".join(e.artefact.title for e in bad[:5]))
            # every entry must itself be an approved version
            unapproved = [
                e.artefact.title for e in entries
                if e.artefact_version.status != RequirementState.APPROVED
            ]
            check("every baseline entry is an approved version", not unapproved,
                  str(unapproved))
            # released / proposed / draft artefacts are NOT baselined
            excluded = sorted(
                a.title for a in db.session.query(Artefact).filter_by(project_id=pid)
                if a.id not in {e.artefact_id for e in entries}
            )
            print(f"        excluded from baseline ({len(excluded)}): {excluded}")
            # second snapshot must be refused by idempotency guard
            from app.services import snapshot_baseline
            again = snapshot_baseline(bl)
            check("baseline re-snapshot is a no-op", again == 0, f"returned {again}")

        # ---- orphans -------------------------------------------------------
        from app.services import orphan_artefacts
        orphans = [a.title for a in orphan_artefacts(pid)]
        check("orphans == F-002 & D-002 only", set(orphans) == {
            "F-002 Quick-release payload platform", "D-002 Intermittent compass calibration drift"},
            str(orphans))

        # ---- relationship types -------------------------------------------
        rt_names = {name for (name,) in db.session.query(RelationshipType.name)}
        check("10 relationship types", len(rt_names) == 10, str(rt_names))

        # ---- reviews -------------------------------------------------------
        reviews = db.session.query(Review).filter_by(project_id=pid).all()
        check("2 reviews", len(reviews) == 2, f"{len(reviews)} != 2")
        for rv in reviews:
            n_assign = db.session.query(ReviewAssignment).filter_by(review_id=rv.id).count()
            n_comm = db.session.query(Comment).filter_by(review_id=rv.id).count()
            print(f"        review '{rv.title}': status={rv.status.value:12s} "
                  f"assignments={n_assign} comments={n_comm}")
        r1 = next((r for r in reviews if "Round 1" in r.title), None)
        if r1:
            check("review-1 has threaded comment (parent/child)",
                  db.session.query(Comment).filter_by(review_id=r1.id, parent_id=None).count() >= 1
                  and db.session.query(Comment).filter(Comment.review_id == r1.id,
                                                       Comment.parent_id.isnot(None)).count() >= 1)
            assigns = {a.reviewer.username: a.role_in_review
                       for a in db.session.query(ReviewAssignment).filter_by(review_id=r1.id)}
            check("review-1 assigned to jane.doe and marc.lee",
                  assigns.get("jane.doe") == "Chair" and assigns.get("marc.lee") == "Reviewer",
                  str(assigns))
            from app.models import Decision
            apprs = db.session.query(Approval).filter_by(review_id=r1.id).all()
            check("review-1 approved by admin",
                  len(apprs) == 1 and apprs[0].reviewer.username == "admin"
                  and apprs[0].decision == Decision.APPROVE)

            # ---- P0: review-from-baseline + verdicts + progress ----------------
            bl1 = db.session.query(Baseline).filter_by(project_id=pid).first()
            check("review-1 is frozen over the baseline",
                  r1.basis_baseline_id == bl1.id, f"basis={r1.basis_baseline_id}")
            from app.services import review_progress, review_scope
            scope = review_scope(r1)
            check("review-1 scope is the 9 baseline artefacts", len(scope) == 9,
                  f"{len(scope)} != 9")
            verdicts = db.session.query(ReviewVerdict).filter_by(review_id=r1.id).all()
            check("review-1 has 8 per-artefact verdicts", len(verdicts) == 8,
                  f"{len(verdicts)} != 8")
            # every verdict must pin an approved (baseline v1) version
            bad_verdict = [
                v for v in verdicts
                if v.artefact_version.version_number != 1
                or v.artefact_version.status != RequirementState.APPROVED
            ]
            check("verdicts pinned to frozen baseline v1", not bad_verdict,
                  [str(v) for v in bad_verdict])
            progress = review_progress(r1)
            check("progress: jane 4/9 and marc 4/9",
                  progress["reviewers"].get("jane.doe", {}).get("decided") == 4
                  and progress["reviewers"].get("marc.lee", {}).get("decided") == 4
                  and progress["overall"] == 44,
                  str(progress))
            # comment on SR-001 must pin the frozen v1, not the live v2
            from app.models import Comment as CommentModel
            frozen_comment = (
                db.session.query(CommentModel)
                .filter(CommentModel.review_id == r1.id, CommentModel.artefact_id.isnot(None))
                .first()
            )
            check("review comment pins frozen baseline version",
                  frozen_comment is not None
                  and frozen_comment.artefact_version_id is not None
                  and frozen_comment.artefact_version.version_number == 1,
                  str(frozen_comment))

        # ---- change requests ----------------------------------------------
        crs = db.session.query(ChangeRequest).filter_by(project_id=pid).all()
        check("3 change requests", len(crs) == 3, f"{len(crs)} != 3")
        for cr in crs:
            items = db.session.query(ChangeRequest).filter_by(project_id=pid).all()
            n_items = sum(1 for c in db.session.query(__import__("app.models", fromlist=["ChangeSetItem"]).ChangeSetItem)
                          if c.change_request_id == cr.id)
        from app.models import ChangeSetItem
        cr1 = next((c for c in crs if "gimbal" in c.title), None)
        if cr1:
            items = db.session.query(ChangeSetItem).filter_by(change_request_id=cr1.id).all()
            check("CR-001 item applied=True", len(items) == 1 and items[0].applied, str(items))

        # ---- artefact comments ---------------------------------------------
        n_ac = db.session.query(Comment).filter(Comment.review_id.is_(None)).count()
        check("artefact-level comments present (2 threaded)", n_ac == 2, f"{n_ac} != 2")

        # ---- users ---------------------------------------------------------
        from app import appbuilder
        for u in ("admin", "jane.doe", "marc.lee"):
            check(f"user '{u}' exists", appbuilder.sm.find_user(username=u) is not None)

        print()
        if FAILURES:
            print(f"{len(FAILURES)} CHECK(S) FAILED")
            return 1
        print("All seed dataset checks passed.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())