# Minimal REQMan PoC Specification (v0.2)

> Status: PoC core + Phase 2 implemented (tracked in §8)
> Scope note: this document tracks the **implemented** scope; the complete roadmap
> (all 12 FRs, Phases 3–5) lives in `Technical_Implementation_Plan.md`.
> Source: derived from `Requirements_Management_Functional_Specification.md`

---

## 1. Purpose

Deliver a **runnable** requirements-management platform on Flask-AppBuilder that
lets a user exercise the whole governance loop end-to-end:

```
Project → Artefact → (immutable version on every change) → Baseline (snapshot of
approved artefact versions) → Review frozen over a baseline (per-artefact verdicts
+ progress) → Traceability / impact analysis → Change management → verify / restore
```

The implemented scope is deliberately kept small enough to prove the data +
workflow invariants (versioning, baselines, review evidence, traceability) before
the larger roadmap (search/reporting, media/AI, enterprise hardening) in
`Technical_Implementation_Plan.md` is built out.

---

## 2. Scope Review — full specification vs. this PoC

### 2.1 Implemented scope

| Functional spec item | Implementation status |
|---|---|
| **FR-1 Project Management** | Create projects only (create/list/show/edit). Archive, clone, templates, permissions deferred. |
| **FR-2 Artefact Management** | Create / edit / delete artefacts with type, status, priority and (plain-text) content. **Artefact views display the properties of the latest version** (kind, title, content, status, priority). Bulk modify, rich text, custom attributes, attachments deferred. |
| **FR-4 Version Management** | **Immutable version snapshot on every save**; version history per artefact; restore a prior version (records a new version). **The live artefact always mirrors its latest version** — the detail page reads from the most recent snapshot. Version comparison (diff view) deferred. |
| **FR-5 Traceability** | Relationship types with kind rules, **validated** trace links, suspect links (set on source change, cleared by a mark-valid action), vis.js **traceability graph**, forward/reverse **impact analysis**, **orphan report**. |
| **FR-6 Baseline Management** | **Create a project Baseline that snapshots the current approved artefact versions in the project** (artefacts whose latest version is not `Approved` are excluded); list/review baseline contents; baseline delete/edit restricted; **create review from baseline**. Compare/restore baselines deferred. |
| **FR-7 Review Management** | Assignments with roles, threaded comments (pinned to the reviewed version), e-signature approvals, workflow (Planned → Active → Comment Resolution → Approved → Closed). **Review-from-baseline** freezes scope and pins verdicts to the baseline versions; per-artefact **verdicts** (Approve/Reject/Abstain) with per-reviewer + overall **progress**. Review reports deferred. |
| **FR-8 Change Management** | Change requests, change-set items (proposed modifications with applied flags), workflow (Submitted → Analysed → Approved → Implemented → Verified → Closed). Applying changes via the versioning service deferred. |
| NFR auditability | `AuditMixin` on all mutable entities + immutable artefact versions + pinned review verdicts = audit trail. | 

### 2.2 Deferred to later phases (out of current scope)

- Project features: archive, clone, templates, owner/lifecycle state, tags, JSON
  import/export, per-project permissions (FR-1).
- Artefact features: rich text, custom attributes, attachments, bulk modify,
  modules / folders / ordered collections + hierarchy numbering (FR-2).
- Version diff / comparison view (FR-4).
- Baseline compare (two baselines / project-vs-baseline), restore-from-baseline,
  baseline lifecycle status + register, configuration-context switching (FR-6).
- Review report (printable), e-signature text on approvals (FR-7).
- Change-set application that auto-versions each applied edit (FR-8).
- Type administration & schemas (FR-3).
- Search & analytics, saved queries (FR-9).
- Reporting & exports (PDF/Word/Excel), trace matrix, audit exports (FR-10).
- REST API and schema migrations via Alembic/Flask-Migrate.
- Media analysis & AI assistance (FR-11/FR-12).
- SSO/MFA, role-based permissions beyond Admin/Public (NFR security).
- 1M+ artefact scalability, Postgres (NFR).

### 2.3 Rationale / gap call-outs from the Technical Implementation Plan

The plan (`Technical_Implementation_Plan.md`) is a **complete multi-phase roadmap** and
is sound in its coverage of all 12 FRs. The implemented scope deliberately **collapses
Phases 0–2** to the minimum governance loop (Phases 3–5 stay on the roadmap). Specific
observations from the review:

1. **Versioning is the make-or-break invariant.** The plan's risk register correctly
   identifies "versioning bypassed by a form POST" as the top risk. The PoC therefore
   enforces version creation **in the view layer hooks** (`post_add` / `post_update`)
   through a service function — every save produces an immutable snapshot. This must
   survive into every later phase.
2. **Baselines must capture *versions*, not live rows.** The plan stores
   `baseline_entry(artefact_id, artefact_version_id)`. The PoC implements exactly this:
   a baseline is un-editable, and its contents are frozen version references.
3. **Phase 2 screens exist; the project-context switcher and module/content screens
   don't.** Reviews (with verdicts pinned to reviewed versions), the traceability
   graph, impact analysis and change requests are implemented as F.A.B. views; the
   plan's *current-project switcher* and ordered module editor remain the main UX
   gaps and are deferred (session context + custom templates).
4. **Simplifications vs. the plan:** plain-text content instead of rich text
   (XSS/bleach deferred to the rich-text phase); no REST API; `create_all` instead of
   Flask-Migrate (schema migration work begins when the full model lands);
   Auth is just F.A.B. default DB auth with an `Admin` role.

---

## 3. Domain Model (implemented)

Thirteen tables (plus F.A.B. security tables).

| Table | Entity | Key columns |
|---|---|---|
| `project` | Project | `id`, `project_code` (unique), `name`, `description`, audit columns |
| `artefact` | Requirement/engineering object | `id`, `project_id → project`, `kind` (enum), `title`, `content` (Text), `status` (enum), `priority` (enum), audit columns |
| `artefact_version` | Immutable snapshot | `id`, `artefact_id → artefact`, `version_number` (per-artefact sequence), `kind`, `title`, `content`, `status`, `priority`, `change_summary`, audit columns |
| `baseline` | Config-controlled snapshot header | `id`, `project_id → project`, `name`, `description`, audit columns |
| `baseline_entry` | Artefact-version-in-baseline | `id`, `baseline_id → baseline`, `artefact_id → artefact`, `artefact_version_id → artefact_version`, unique(baseline, artefact) |
| `relationship_type` | Link-type definition | `id`, `name` (unique), `display_name`, `is_directional`, `source_kinds` / `target_kinds` (JSON kind rules) |
| `artefact_relationship` | Validated trace link | `id`, `project_id`, `source_id → artefact`, `target_id → artefact`, `relationship_type_id`, `is_suspect`, unique(source, target, type) |
| `review` | Formal review | `id`, `project_id`, `title`, `description`, `status`, `due_date`, `basis_baseline_id → baseline` (nullable — frozen review), audit columns |
| `review_assignment` | Reviewer/role | `id`, `review_id`, `reviewer → ab_user`, `role_in_review`, `response_status` |
| `review_verdict` | Per-artefact decision | `id`, `review_id`, `reviewer → ab_user`, `artefact_id`, `artefact_version_id` **pinned**, `decision` (Approve/Reject/Abstain), `comment`, unique(review, reviewer, artefact) |
| `comment` | Threaded discussion | `id`, `review_id` (nullable), `artefact_id` (nullable), `artefact_version_id` (pinned under review), `parent_id → comment`, `body`, `resolved` |
| `approval` | E-signature record | `id`, `review_id`, `reviewer → ab_user`, `decision`, `comment` |
| `change_request` | Change control | `id`, `project_id`, `title`, `description`, `status`, `priority`, `requested_by → ab_user` |
| `change_set_item` | Proposed modification | `id`, `change_request_id`, `artefact_id`, `action` (Add/Update/Delete), `proposed_change`, `applied` |

**Constraints / rules**

- `artefact_version.version_number` is strictly increasing per artefact (max+1).
- Version rows are **append-only**: no edit/delete routes are exposed.
- A baseline entry references the **current approved artefact version** at snapshot
  time: only artefacts whose latest version is in the `Approved` state are baselined
  (unapproved artefacts are skipped).
- An artefact's **displayed properties always come from its latest version**; the live
  `artefact` row is a mirror of version N (and a restored version N+1 after restore).
- A baseline is **immutable after creation** (edit/delete routes disabled).
- An artefact referenced by any baseline entry cannot be hard-deleted
  (guarded in the view hook); this preserves baseline evidence.
- Trace links are validated before insert (same project, no self/duplicate links,
  kind rules); editing an artefact marks its **outgoing links suspect** until a
  mark-as-reviewed action clears the flag.
- A review may be **frozen over a baseline** (`basis_baseline_id`): its scope is the
  baseline's artefacts and every verdict/artefact comment then resolves to the
  **baseline's version** of the artefact — never the live row. Without a basis, the
  latest version at decision time is pinned instead.
- `review_verdict` holds **one decision per (review, reviewer, artefact)**;
  re-recording replaces the verdict; decision progress is computed over the review
  scope (baseline entries or all project artefacts).

---

## 4. Functional Requirements (PoC)

### FR-P1 — Projects
- Manage projects (create, list, show, edit).
- Project `project_code` is unique.

### FR-P2 — Artefacts
- Create, edit, delete artefacts within a project.
- Each artefact has a kind, title, content, status and priority. The **add form
  deliberately omits status**: a new artefact is `Draft`, and reaching `Approved`
  is an explicit action on the artefact page (the FR-P4 baseline gate).
- Status choices mirror the requirement lifecycle
  (Draft → Proposed → In Review → Approved → Implemented → Verified → Released →
  Obsolete); PoC stores them as an enum, no transition engine yet.

### FR-P3 — Versioning (immutable)
- Every artefact create produces **version 1** ("Initial draft").
- Every artefact edit that changes versioned content (kind, title, content, status,
  priority) produces a new version (max+1, "Edited"). An unchanged Save records
  nothing, so history reflects real changes only.
- Versions record kind, title, content, status, priority, change summary, author
  and timestamp.
- Versions are read-only in the UI.
- **Display invariant:** an artefact displays the properties of its **latest**
  Artefact Version — kind, title, content, status and priority always come from
  version N (the detail page reads the most recent snapshot).
- **Restore:** an old version can be promoted back to the artefact; this writes a new
  version ("Restored from vN") — history is never rewritten. After restore the
  artefact displays the restored content as the new latest version.

### FR-P4 — Baselines (project snapshots of approved artefact versions)
- The user can create a **Baseline from a Project** that baselines **all the current
  approved artefact versions** in that project.
- Only artefacts whose latest version is in the `Approved` state are included;
  artefacts still in Draft / Proposed / In Review / etc. are skipped.
- Each baseline entry freezes the exact approved version that was current at snapshot
  time.
- View baseline contents: the exact artefact versions frozen in the baseline.
- Baselines cannot be edited or deleted in the PoC UI.

### FR-P5 — Minimum workflow acceptance
The implementation is accepted when the following loop is demonstrated:
1. Create a project.
2. Create two artefacts in the project (both at version 1, status `Approved`).
3. Edit artefact A (A now at version 2, still `Approved`).
4. Create a baseline → baseline contains artefact A **v2** and artefact B **v1**.
5. Edit artefact A again (A at v3), re-open the baseline → it still shows A **v2**.
6. Create an artefact C (status `Draft`) → a new baseline skips C (not approved).
7. Restore artefact B from v1 → B gains a **v2** whose content equals the restored
   values; original history intact.
8. Create a review **from the baseline**, assign a reviewer, record an Approve /
   Reject / Abstain verdict on one artefact → the review shows decision progress
   per reviewer and overall, and the verdict pins the baseline version even if the
   artefact is edited afterwards.

### FR-P6 — Traceability
- Relationship types define display name, directionality and optional kind rules
  (JSON lists of allowed artefact kinds per end).
- Trace links are validated (same project, no self/duplicate links, kind rules)
  before they are saved.
- Editing an artefact marks its outgoing links **suspect**; a *mark as reviewed*
  action clears the flag.
- **Traceability graph** (vis.js) centred on any artefact, with double-click to open
  a node; **impact analysis** forward/backward with a user-set depth (1–5 hops);
  **orphan report** for artefacts with no links at all. Both ends of every link are
  shown on the artefact page, and every report row links onward.

### FR-P7 — Reviews and verdicts
- Reviews follow Planned → Active → Comment Resolution → Approved → Closed with
  guarded transitions, driven by **one "Next step" button** plus **"Send back for
  comment"** for the Active → Comment Resolution branch (no state needs the raw
  status field any more).
- Assignments carry roles (Chair / Reviewer / …) and response status.
- Threaded comments on reviews and on individual artefacts; artefact comments
  under review pin the reviewed version.
- Approvals capture the current user as the electronic signature + decision +
  comment, filed with a single click ("Sign off (approve)" / "Sign with dissent")
  and stored as read-only evidence; re-signing replaces that reviewer's own
  position rather than duplicating it.
- **Review-from-baseline:** a review may be frozen over a baseline — its scope is
  the baseline artefact set and verdicts/comments resolve to the frozen baseline
  versions.
- **Verdicts:** one Approve / Reject / Abstain decision per reviewer and artefact
  (optional notes); re-recording replaces the decision.
- **Progress:** review list and show pages display per-reviewer and overall
  decision progress.

### FR-P8 — Change management
- Change requests with title, description, priority, requested-by (current user)
  and workflow Submitted → Analysed → Approved → Implemented → Verified → Closed,
  walked with **one "Next step" button** (the path is linear, so exactly one move
  is legal at a time).
- Change-set items propose Add / Update / Delete on artefacts with a proposed
  change text and an `applied` flag.
- All transitions go through the workflow guard (`can_transition`); the
  "Next step" map is asserted against the transition table by tests.

---

## 5. Roles & Security (PoC)

- `AUTH_DB` (existing config). All logged-in users with the built-in `Admin` role can
  use the PoC; no custom roles are introduced yet.
- CSRF stays enabled; `WTF_CSRF_ENABLED` is only disabled in tests.

---

## 6. User Interface & Workflow Design (PoC)

F.A.B. `ModelView`s, `GroupByChartView` dashboards, a custom `IndexView` landing
page and row `Action`s (all single-record: they appear on the record page, never
as a bulk list action, and each one returns the user to the record they acted
on); custom templates only for the landing page, trace graph, impact and orphan
screens.

The menu is ordered as the governance loop (the order of the `add_view` /
`add_link` calls *is* the menu order), and the indented entries below are
**saved filters** — one-click list URLs (`?_flt_0_<col>=<value>`) that can still
be searched, ordered and linked to:

| Menu | View | UX notes |
|---|---|---|
| **Home** (`/`) | `HomeView` (`IndexView`) | The loop in four cards (capture → approve & baseline → review → analyse & change) plus live counters: projects, artefacts, draft, approved, baselines, open reviews, open changes, suspect links, untraced artefacts. Every tile links to the list or saved filter that explains it. |
| **Requirements → Projects** | `ProjectView` | The one project screen. CRUD; show page is a **project workspace** (tabs: artefacts, baselines, reviews, change requests). Row action **Create Baseline from project** snapshots the project's current approved versions in one click. |
| **Requirements → Artefacts** | `ArtefactView` | List (ref, project, type, title, lifecycle, priority, last changed), 50 per page, grouped by project. The **add form has no status** (new artefacts are Draft) and every form field carries the hint that explains the invariant. Row actions **Approve artefact** (→ `Approved`, versioned, baselinable), **Mark obsolete** (retire it with a version — baselined or trace-linked artefacts cannot be deleted), **Open traceability graph**, **Impact report** and **Add trace link from here**. Show tabs: **Versions**, **Discussion**, **Traces (out)**, **Traces (in)**; the *Record* group (authorship) is collapsed. |
| ↳ *Draft artefacts* | saved filter | `?_flt_0_status=DRAFT` — the artefacts still needing work. |
| *(no menu)* | `ArtefactVersionView` | Read-only (list/show only). **Restore** action button. Accessible via the artefact show page Versions tab. |
| *(no menu)* | `ArtefactOutgoingLinkView` | Read-only outgoing trace links per artefact (relationship, target, suspect flag). |
| **Requirements → Trace Links** | `RelationshipView` | Validated links; **Mark as reviewed** action; suspect links read "Suspect - re-check" instead of `True`. Row add validates kind rules and explains the arrow direction. |
| **Governance → Baselines** | `BaselineView` | Add = pick a project; the name is optional and gets the same date-stamped `<code> BL <date>` name the project page's one-click action uses. On save the app snapshots that project's current **approved** artefact versions, names anything it skipped, and **refuses to keep an empty baseline**. Row action **Create review from baseline** (DNG pattern). Edit/Delete disabled. Show tab: **Contents**. |
| *(no menu)* | `BaselineEntryView` | Read-only. Shows the artefact + frozen version per baseline, accessible via the baseline show page Contents tab. |
| **Governance → Reviews** | `ReviewView` | List shows live **decision progress** (per reviewer + overall; blank reads "Not started - no reviewers yet"). Show tabs: **Verdicts**, **Reviewers**, **Discussion**, **Signatures**. Actions: **Next step** (Planned → Active → Approved → Closed), **Send back for comment** (Active → Comment Resolution), **Sign off (approve)**, **Sign with dissent**. `basis_baseline` optional — empty means "Live project content", pick a baseline to freeze the review. |
| ↳ *Active reviews* | saved filter | `?_flt_0_status=ACTIVE` — reviews in flight. |
| *(no menu)* | `ReviewVerdictView` | Per-artefact verdicts (Approve / Reject / Abstain), added from the review's **Verdicts** tab — which carries the review with it, so the only choices left are the artefact and the decision. The reviewer is the signed-in user; each verdict pins the **reviewed version** (frozen baseline version when the review is baseline-based) so evidence survives later edits. |
| *(no menu)* | `ApprovalView` | **Read-only evidence**: signatures are made with the review page's **Sign off** / **Sign with dissent** buttons, so the signer is always whoever clicked and no approval can be filed against the wrong review. Re-signing replaces that reviewer's own position instead of stacking duplicates. |
| **Governance → Change Requests** | `ChangeRequestView` | One **Next step** action drives the whole path (Submitted → Analysed → Approved → Implemented → Verified → Closed); **Change items** tab; requested-by = current user. |
| ↳ *Open change requests* | saved filter | `?_flt_0_status=SUBMITTED` — changes waiting to be analysed. |
| *(no menu)* | `ArtefactIncomingLinkView` | Read-only **incoming** trace links per artefact (link type, the artefact it is traced from, suspect flag). Pinned to the `target` end by a one-method `SQLAInterface` subclass — F.A.B. otherwise hands both tabs the first relation back to `Artefact`. |
| **Analysis → Traceability Graph** | `TraceGraphView` | vis.js graph centred on any artefact (nodes coloured by kind/status, edges labelled, dashed when suspect). Double-click a node to open that artefact; links to the impact report and to the artefact page; the embedded picker defaults to the artefact's own project. |
| **Analysis → Impact Report** | `ImpactAnalysisView` | Forward/backward impact with a **depth control (1–5 hops, clamped)**; every impacted row links to its artefact, its graph and its own impact report at the same depth; suspect rows link to the link itself so it can be marked reviewed; ends with a picker to analyse the next artefact. |
| ↳ *Suspect trace links* | saved filter | `?_flt_0_is_suspect=1` — links whose source moved after the link did. |
| ↳ *Orphan report* | saved page | `/impactview/orphans/` — artefacts with no trace links at all, grouped per project with counts; each row links to the artefact, to *trace it* (a pre-filled link form) and to its impact report. |
| **Dashboard → Artefact Mix** | `ArtefactDistributionChartView` | `GroupByChartView` pie of artefacts by kind and by status. |
| **Dashboard → Artefacts per Project** | `ArtefactPerProjectChartView` | `GroupByChartView` column chart per project. |
| **Administration → Link Types** | `RelationshipTypeView` | Name, display name, directionality, optional kind rules (JSON). |

Registering views also runs `prune_stale_permissions()` (`app/security.py`), which
deletes security rows left behind when a view class, menu entry or row action is
renamed or removed — role links first, then the permission-view rows, then the
orphaned permissions and view menus. F.A.B.'s own `security_cleanup()` is **not**
used: it deletes a stale permission-view by handing the *Permission* to
`del_permission_role()`, so the role link survives, the delete is refused, and
every boot logs "Refused to delete permission view, assoc with role exists".
`prune_stale_permissions()` is idempotent and logs at INFO what it removed.

**Form and list conventions (Milestone 2):**

- Screens name themselves: `list_title` / `show_title` / `add_title` / `edit_title`
  are set on every view, so related-view tabs read *Versions*, *Contents*,
  *Verdicts*, *Reviewers*, *Discussion*, *Signatures*, *Traces (out)* rather than
  F.A.B.'s generated "List Review Verdict".
- Fieldsets are the **single source of truth** for add/edit/show fields (F.A.B.
  derives the `*_columns` lists from them) and every group title is a translatable
  string. Secondary groups (*Record*, evidence/authorship) render collapsed.
- `search_columns` is declared on every view (1–5 fields). No audit column, long
  text blob or to-many relationship is searchable — F.A.B. silently ignores such
  filters, and `"Filter column not allowed"` was the old behaviour on 9 of 13 views.
- `description_columns` states the invariant next to the field that enforces it
  (versioning on save, baseline scope, review basis, trace direction, verdict scope).
- `formatters_columns` keeps storage values off the screen: timestamps as
  `22 Sep 2026 10:01`, dates as `22 Sep 2026`, empties as `—`, booleans as
  *Checked* / *Suspect - re-check*, *Resolved* / *Open*, *One way* / *Either way*,
  *Applied* / *Not applied yet*.

**Action conventions (Milestone 3):**

- **One button per legal move.** `REVIEW_ADVANCE` / `CHANGE_ADVANCE` in
  `app/services.py` say what the single **Next step** button does from each state,
  and every entry is asserted legal in the `REVIEW_TRANSITIONS` /
  `CHANGE_TRANSITIONS` tables — so the button and the guard can never drift apart.
  A change request used to show Analyse/Approve/Implement/Verify/Close at once,
  four of which could only fail; a review showed three.
- **Branches get their own named button.** `Send back for comment` (Active →
  Comment Resolution) is the one move the linear path cannot take; that state is
  now reachable from the UI, which is why `status` left the review edit form.
- **Signing is a click, not a form.** `Sign off (approve)` / `Sign with dissent`
  file the approval as the signed-in user (FR-P7) and leave the review's state
  alone — the chair still moves it on. `ApprovalView` is read-only evidence.
- **No 500s from stale or bulk action calls.** `SingleRecordActionMixin` answers an
  unknown action name (old bookmark after a rename) or a bulk `action_post` with a
  warning and a redirect. Note `@has_access` is deliberately *not* on those two
  handlers: it demands `can_action`/`can_action_post` membership in
  `base_permissions`, which turns the friendly warning into a bare 403 on the
  restricted views.
- **Adding from a parent's tab carries the parent.** F.A.B.'s
  `?_flt_0_<relation>=<pk>` add links hide that field and fill it server-side, so
  a comment or verdict made from a review page can only land on that review.
- **Both baseline doors behave identically**: same date-stamped name, same
  "captured N / skipped M (named)" feedback, and neither keeps an empty baseline.

**Traceability & analysis conventions (Milestone 4):**

- **No dead ends.** Every artefact named by an impact report, orphan report or
  suspect-link table links to that artefact's page, its graph and its own impact
  report, so analysis can be followed hop by hop instead of re-entered by hand. The
  graph and the impact report link to each other for the same artefact.
- **Pickers are scoped and capped** (`analysis_picker()` + `_analysis_picker.html`):
  choose a project first, then one of its artefacts (first `PICKER_LIMIT` = 200, with
  a note when truncated). Nothing loads every artefact in the database, and result
  pages pre-select the project of the artefact you came from.
- **Depth is a control, not a constant**: `?depth=1..5`, clamped and defaulted, and
  the current depth is kept when re-analysing from a row.
- **Both ends of a link are visible** from an artefact page — *Traces (out)* and
  *Traces (in)* — the latter pinned to the `target` end by a one-method
  `SQLAInterface` subclass (F.A.B. otherwise hands both tabs the first relation).
- **Adding a link starts from the artefact in hand**: *Add trace link from here*
  opens the form with `?_flt_0_source=<id>`, so F.A.B. hides that end and the only
  choices left are the link type and the far end; `project_id` is derived from the
  pinned source and cross-project/self/duplicate links are still refused.

**User journey (primary workflow):**

1. Home → **Projects** → create a project.
2. Requirements → Artefacts → Add (select project, kind, title, content, priority;
   status is not on the form). The system creates version 1 automatically; the
   artefact page shows version 1's properties.
3. Edit the artefact → the system creates version 2; the artefact page now shows
   version 2's properties. Use the **Approve** row action (or edit the status) to
   move it to `Approved`.
4. Open the artefact detail → the displayed properties come from the **latest** version;
   the **Versions** tab shows the full immutable history with a Restore button per version;
   the **Traceability** tab lists the outgoing links.
5. Create a baseline in **one click** from the project's row action, or via
   Governance → Baselines → Add → pick the project → **Save**. Either path snapshots the
   current **approved** artefact versions (unapproved artefacts are not baselined).
6. Open the baseline detail → **Contents** tab shows exactly which approved artefact
   versions are frozen in it. Use the row action **Create review from baseline** to
   open a review whose verdicts/comments freeze to the baseline versions.
7. Reviewers record an **Approve / Reject / Abstain** verdict (with notes) per artefact
   from the review's **Verdicts** tab (which carries the review with it); the review
   list and show page display **decision progress** per reviewer and overall. The
   chair signs with **Sign off (approve)** / **Sign with dissent** and moves the
   review on with **Next step**.
8. Requirements → Trace Links → add validated links between artefacts; the artefact
   detail shows its outgoing links; Analysis → Traceability Graph / Impact Report
   visualise the network, suspects and orphans (Analysis → *Suspect trace links* and
   *Orphan report* jump straight to the work).
9. Governance → Change Requests → create a request, add change-set items, then press
   **Next step** until it reaches Closed.
10. Home summarises the state of the whole loop with live counters; Dashboard
    charts show the artefact distribution at a glance.

---

## 7. Out of Scope (explicit)

Anything not listed in §4 is deferred to later phases (see §2.2 for the full list),
most notably: rich text and attachments, modules/collections, type administration,
version diff, baseline compare/restore, review reports, search + saved queries,
reporting/export (trace matrix, PDF/Word/Excel), media/AI, REST API, SSO/MFA,
role-based permissions beyond Admin, and schema migrations via Alembic.

---

## 8. Definition of Done — status

**Implemented (PoC core + Phase 2):**

- [x] Models `project`, `artefact`, `artefact_version`, `baseline`, `baseline_entry`,
      `relationship_type`, `artefact_relationship`, `review`, `review_assignment`,
      `review_verdict`, `comment`, `approval`, `change_request`, `change_set_item`
      covered by pytest (217 tests, of which the UI suite in
      `test_ui_defects/navigation/layout/actions/traceability` covers the interface
      and workflow conventions of Milestones 0–4).
- [x] Artefact saves produce immutable versions; versions are read-only with a
      **Restore** action (records a new version, never rewrites history).
- [x] Artefact views display the properties of the latest version (live row mirrors
      version N).
- [x] Baselines snapshot the current **approved** artefact versions only
      (unapproved artefacts excluded); contents re-openable; re-snapshot refused.
- [x] FR-P5 acceptance loop plus traceability, review-verdict and change-workflow
      tests are green.
- [x] App boots via `run.py` with the menu ordered as the loop: Requirements
      (Projects, Artefacts, Trace Links, *Draft artefacts*), Governance (Baselines,
      Reviews, *Active reviews*, Change Requests, *Open change requests*), Analysis
      (Traceability Graph, Impact Report, *Suspect trace links*, *Orphan report*),
      Dashboard (Artefact Mix, Artefacts per Project), Administration (Link Types),
      plus the `HomeView` landing page.

**Open (later phases — full list in §2.2):** rich text, custom attributes,
attachments, bulk modify, modules/collections (FR-2), type administration (FR-3),
version diff (FR-4), baseline compare/restore + lifecycle status, configuration
context (FR-6), review reports (FR-7), change-set auto-versioning (FR-8),
search + saved queries (FR-9), reporting/export (FR-10), media/AI (FR-11/12),
REST API, migrations, RBAC/SSO/MFA and Postgres-scale NFRs.