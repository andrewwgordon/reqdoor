# REQMan (reqdoor)

Requirements Management concept solution — a web application for capturing,
tracing, baselining, reviewing and changing requirement artefacts under control.

Built with [Flask-AppBuilder](https://flask-appbuilder.readthedocs.io/) on
Flask + SQLAlchemy. REQMan stores every real change to an artefact as an
immutable version, freezes approved content into baselines, runs electronic
review workflows over them, and manages change requests with per-item
accountability — so decisions always point at the evidence that was actually
judged.

## Features

- **Capture** — projects and artefacts (requirements, use cases, risks, test
  cases, …) with a Draft → Approved → Obsolete lifecycle and immutable version history.
- **Traceability** — typed, validated, directed trace links with a suspect flag,
  an interactive graph, impact reports and an orphan report.
- **Baselines** — immutable snapshots of a project's approved artefact versions.
- **Reviews** — DNG-style reviews with reviewer assignments, per-artefact
  verdicts (Approve / Reject / Abstain), threaded comments and one-click
  electronic signatures.
- **Change management** — change requests driven through a linear
  Submitted → … → Closed workflow with individual change items.
- **Overview** — a landing page with live counters and charts of the artefact mix.
- **Paging** — every row-based list pages at 25 rows per page (configurable
  limit is enforced by the test suite; see `PAGE_SIZE` in `app/views.py`).

## Quick start

Prerequisites: **Python ≥ 3.14** (see `pyproject.toml`). The project is set up
for [uv](https://docs.astral.sh/uv/); plain `pip` works too.

```bash
# 1. Install dependencies
uv sync

# 2. Start the development server  →  http://localhost:8080
uv run python run.py

# 3. (Optional) Load the demonstration dataset and demo users
uv run python -m app.seed create
```

Log in with one of the seeded accounts:

| Username  | Password  | Role |
|-----------|-----------|------|
| `admin`   | `admin`   | System Admin (everything) |
| `jane.doe`| `password`| Admin role (demo reviewer) |
| `marc.lee`| `password`| Admin role (demo reviewer) |

Without the seed, the database starts empty. Create the first admin account with
`flask fab create-admin` (set `FLASK_APP=run.py` first if your shell does not
pick it up), then add users under **Admin → Security → List Users** (self
registration is disabled by default).

Run the test suite (218 tests, workflow + UI regression):

```bash
uv run pytest
```

## Technical overview

### Stack

| Layer       | Technology |
|-------------|------------|
| Web framework | Flask 3.x, Flask-AppBuilder ≥ 5.2 (CRUD views, auth, menus, permissions) |
| ORM / database | SQLAlchemy 2.x via F.A.B.'s `SQLA`; SQLite by default (`app.db`), MySQL/PostgreSQL via `SQLALCHEMY_DATABASE_URI` |
| Templates / UI | Jinja2, Bootstrap (Bootswatch *slate* theme), vis.js network graph |
| Auth          | F.A.B. database auth (`AUTH_DB`), role-based permissions |
| i18n          | Flask-Babel — every UI string is marked for translation (`_()`); en is the default and the catalog can be extracted/compiled with `flask fab babel-extract` / `babel-compile` |
| Tests         | pytest against a live test app + seeded in-memory SQLite |

### Repository layout

```
config.py          # all app configuration (secret key, DB URI, auth, theme, index view)
run.py             # dev launcher — http://0.0.0.0:8080
app/
  __init__.py      # create_app() factory: config → db → appbuilder → views → create_all
  extensions.py    # shared db / appbuilder singletons
  models.py        # schema only: 13 tables + the workflow enums (no business rules)
  services.py      # every business rule + the guarded state-transition tables
  views.py         # ModelViews, actions, charts, landing page, menu registration
  seed.py          # idempotent HX4 demo dataset generator (CLI: create / destroy)
  security.py      # permission pruning on boot (renamed views/actions)
  templates/       # landing page, trace graph, impact/orphan reports, pickers
docs/              # domain model analysis, functional spec, implementation plan
tests/             # 218 pytest tests: models, services, workflows, UI regression
scripts/           # standalone seed verification
```

### Architecture

The domain model is organized in four layers (all project-scoped):

1. **Scope** — `Project`
2. **Content** — `Artefact` (one live row) + `ArtefactVersion` (append-only history)
3. **Structure** — `RelationshipType` + `ArtefactRelationship` (typed trace links)
4. **Governance** — `Baseline`/`BaselineEntry`, `Review`/`ReviewAssignment`/
   `ReviewVerdict`/`Approval`/`Comment`, `ChangeRequest`/`ChangeSetItem`

Design rules that keep the data trustworthy:

- **Evidence never points at a live row.** Baseline entries, review verdicts and
  review comments reference an `ArtefactVersion`, so a decision stays about what
  was actually reviewed even after the artefact moves on (`review_artefact_version`
  in `app/services.py`).
- **`app/models.py` is schema only.** All invariants — link validation, delete
  protection, versioning, state transitions — live in `app/services.py` so no
  call site can bypass them; the UI surfaces them through guarded row actions
  (`actions.py`-style `@action` handlers in `app/views.py`).
- **Guarded workflows.** Reviews and change requests move through explicit
  transition tables (`REVIEW_TRANSITIONS`, `CHANGE_TRANSITIONS`); the single
  "Next step" button only ever attempts the one legal move, and "Send back for
  comment" is the only branch off the linear path.
- **Read-only where the record is evidence.** Version history, baseline
  contents, signatures and trace-link views expose only `list`/`show`
  permissions; edit/delete routes are excluded.
- **25-row pages.** Every row-based view sets `page_size = PAGE_SIZE` (25) —
  also enforced by `tests/test_ui_layout.py`.

### Configuration (`config.py`)

| Key | Default | Notes |
|-----|---------|-------|
| `SECRET_KEY` | hard-coded dev value | **change it before any real deployment** |
| `SQLALCHEMY_DATABASE_URI` | `sqlite:///app.db` | swap for MySQL/PostgreSQL |
| `AUTH_TYPE` | `AUTH_DB` | LDAP/OAuth config is scaffolded, commented out |
| `APP_NAME` | `REQMan` | shown in the navbar |
| `FAB_INDEX_VIEW` | `app.views.HomeView` | landing page with governance counters |
| `APP_THEME` | `slate.css` | Bootswatch theme |

Schema is created with `create_all()` on boot — there is no Alembic migration
tooling yet, so model changes do not upgrade an existing database automatically.
There is no REST API yet (the functional spec reserves `/api/v1` for a future
`ModelRestApi` layer).

## User guide

### Signing in

Open `http://localhost:8080`, log in with an account above. Anonymous visitors
see the landing page and the sign-in prompt; all data screens require a role
with the matching permission. The **Home** page shows live counters — projects,
artefacts (draft / approved), baselines, open reviews, open change requests,
suspect links and orphan artefacts — each linking to the relevant list.

### The governance loop

The menu is organized in workflow order. A healthy control cycle looks like:

```
Capture  →  Govern  →  Analyse  →  Overview  →  Configure
(artefacts,   (baselines,     (graph, impact,   (charts)   (link types)
 trace links)  reviews,        suspect, orphan
               changes)        reports)
```

### 1. Capture — projects and artefacts

1. **Projects** (`Requirements → Projects`) — add a project (code + name).
   Everything else is scoped to a project.
2. **Add an artefact** (`Requirements → Artefacts`) — pick the project, kind,
   title and content. New artefacts start as **Draft** and the initial draft is
   versioned automatically.
3. **Approve** — use the *Approve artefact* action on the artefact page to move
   it to **Approved** (this records a version and makes it baselinable). Edit
   the artefact freely before then; every real change is saved as a new
   immutable version — history is never overwritten.
4. **Versions** — the *Versions* tab on an artefact shows the full history; any
   version can be restored (the artefact moves back to that content and a new
   version is recorded).
5. **Retire, don't delete** — *Mark obsolete* replaces deletion: an artefact
   that is frozen in a baseline or has trace links cannot be deleted (the UI
   explains why). Obsolete artefacts stay in history and in baselines.

### 2. Trace links

`Requirements → Trace Links` — create typed links (e.g. *Verifies*, *Refines*)
between artefacts in the same project. The link type defines which artefact
kinds are allowed at each end, and the project is inherited from the source
artefact.

Editing the source of a link marks the link **Suspect**. Re-check it and use
*Mark as reviewed* to clear the flag. Suspect links are highlighted in the
traceability graph and collected under `Analysis → Suspect trace links`.

### 3. Baselines

A baseline is an immutable snapshot of a project's *current approved* artefact
versions. Two doors:

- On the **project page**: *Create Baseline from project* (one click, name
  auto-generated like `HX4 BL 12 May 2026`).
- Under `Governance → Baselines → Add`: pick the project (name is optional).

Baselines cannot be edited or deleted — the *Contents* tab shows the frozen
versions (title, kind, content, version number) exactly as they stood. An
empty baseline is refused: "Only artefacts whose latest version is Approved
are captured."

### 4. Reviews

Reviews judge content with evidence. A review pages through
**Planned → Active → Comment Resolution → Approved → Closed**.

1. **Create** (`Governance → Reviews`) — title, project, due date, and either a
   *basis baseline* (frozen review: verdicts pin the baseline versions) or no
   basis (live project content).
2. **Assign reviewers** — the *Reviewers* tab adds assignments with a role
   (Chair, Reviewer, Scribe) and response status.
3. **Next step** → the review becomes **Active**.
4. **Reviewers record verdicts** — the *Verdicts* tab: one verdict per artefact
   in scope (Approve / Reject / Abstain), with the reviewed version shown.
   Reviewers comment on artefacts or the review via the *Discussion* tab
   (threaded, with a *Resolved* flag).
5. **Signatures** — the chair (any signed-in user) clicks **Sign off** or
   **Sign with dissent** on the review page; the signature records the account
   and the time as an electronic record under the *Signatures* tab.
6. **Branch** — an Active review can be *Sent back for comment* while the
   authors resolve comments; afterwards it returns to Approved via *Next step*.
7. Close it out: **Next step** → Approved → Closed.

### 5. Change requests

Changes are proposed, judged and tracked per item:

1. **Create** (`Governance → Change Requests`) — project, title, priority. The
   requester is recorded automatically.
2. **Add change items** — the *Change items* tab on the request: one item per
   artefact with an action (Add / Update / Delete) and the proposed wording.
3. **Next step** walks the request through
   **Submitted → Analysed → Approved → Implemented → Verified → Closed** —
   exactly one legal move is offered at a time.
4. As items are applied to artefacts by hand, tick *Applied* on each item so
   the request always shows what is still outstanding.

### 6. Analysis

- **Traceability Graph** (`Analysis`) — pick an artefact to see its network out
  to two hops; double-click a node to jump to it. Suspect links are drawn dashed.
- **Impact Report** — forward + reverse impact of an artefact, with a
  depth selector (1–5). Suspect links in the affected set are listed separately.
- **Orphan report** — artefacts with no trace links at all.
- One-click saved filters under the lists (Draft artefacts, Active reviews,
  Open change requests, Suspect trace links) keep the most useful queries one
  click away — and they can be searched and ordered further.

### 7. Dashboard and administration

- **Artefact Mix** and **Artefacts per Project** (`Dashboard`) — distribution
  charts of the captured content.
- **Link Types** (`Administration`) — create and edit the vocabulary of trace
  links, including the kind rules that constrain each end. Seeded types cover
  the usual DO-178C-style set (Satisfies, Refines, Implements, Verifies,
  Mitigates, Conflicts With, …).

### Working with long lists

Every list page shows **at most 25 rows** and pages the rest — use the search
panel to filter, and the column headers to order. Master/detail tabs (versions,
baseline contents, verdicts, signatures, change items) page the same way.

### Resetting the demo data

```bash
uv run python -m app.seed destroy   # removes only seed-created rows (+ demo users)
uv run python -m app.seed create    # recreate; a no-op if HX4 already exists
uv run python -m scripts.verify_seed  # standalone integrity report on the seed
```

## Documentation

- `docs/domain_model.md` — the schema, invariants and known risks in depth
- `docs/minimal_spec.md`, `docs/Requirements_Management_Functional_Specification.md`
  — the requirements this solution implements (FR-P1…FR-P8 etc.)
- `docs/Technical_Implementation_Plan.md` — the build plan and milestone map