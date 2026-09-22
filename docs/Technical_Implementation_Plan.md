# Technical Implementation Plan
## Enterprise Requirements Management Tool (REQMan / reqdoor)

Built on **Flask-AppBuilder 5.2+** (F.A.B.), Flask 3.x, SQLAlchemy 2.x, Python 3.14.

> **Source specification:** `docs/Requirements_Management_Functional_Specification.md`
> **Target stack baseline:** current `reqdoor` project (`ProjectView` already registered).

> **Implementation status**
> - **Phase 0/PoC (done):** Project → Artefact → immutable versions → Baselines of
>   artefact versions. Scope, rationale and acceptance criteria in
>   `docs/minimal_spec.md`.
> - **Phase 2 (done):** Traceability (relationship types, validated links, suspect
>   links, trace graph via vis.js, impact analysis, orphan report), Reviews
>   (assignments, threaded comments, approvals, workflow actions) and Change
>   Management (change requests, change-set items, workflow actions).

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Current State Assessment](#2-current-state-assessment)
3. [Guiding Principles](#3-guiding-principles)
4. [Technology Stack and New Dependencies](#4-technology-stack-and-new-dependencies)
5. [Target Application Structure](#5-target-application-structure)
6. [Configuration Changes](#6-configuration-changes)
7. [Data Model Design](#7-data-model-design)
8. [Flask-AppBuilder View Architecture and UI Workflow Design](#8-flask-appbuilder-view-architecture-and-ui-workflow-design)
9. [Functional Requirement Implementation Matrix](#9-functional-requirement-implementation-matrix)
10. [Use Case to Screen-Flow Mappings](#10-use-case-to-screen-flow-mappings)
11. [Security and Role-Based Access](#11-security-and-role-based-access)
12. [API Layer](#12-api-layer)
13. [Search, Reporting and Analytics](#13-search-reporting-and-analytics)
14. [Media Analysis and AI Assistance](#14-media-analysis-and-ai-assistance)
15. [Non-Functional Requirements Strategy](#15-non-functional-requirements-strategy)
16. [Schema Management and Migrations](#16-schema-management-and-migrations)
17. [Testing Strategy](#17-testing-strategy)
18. [Phased Delivery Roadmap](#18-phased-delivery-roadmap)
19. [Risks and Mitigations](#19-risks-and-mitigations)
20. [Open Design Decisions](#20-open-design-decisions)

---

## 1. Purpose and Scope

This plan translates the functional specification into a concrete, buildable
architecture on Flask-AppBuilder, reusing the current project skeleton. It covers:

- **Data model** — all core and extended domain entities as SQLAlchemy models.
- **View architecture** — the complete catalogue of F.A.B. views (ModelView,
  MasterDetailView, BaseView, SimpleFormView, charts, REST APIs) and how they are
  composed into screens.
- **UI workflow design** — navigation, menu structure, per-screen behaviour, and the
  end-to-end user journeys for every priority use case.

### Delivery boundary / phasing

The full specification (12 functional requirements, AI/media analysis, SSO,
multi-million-artefact scale) cannot be delivered in a single increment. This plan
defines a **phased roadmap** (Section 18) and explicitly separates **core RM
capability** (Phases 1–3) from **extended capabilities** (Phases 4–5) that rely on
external services. Every screen and model described here is designed so that later
phases extend rather than rework earlier ones.

---

## 2. Current State Assessment

| Area | Current state | Gap to specification |
|---|---|---|
| App factory | `create_app()` with optional `config_overrides`, idempotent view registration | Needs to register ~15 view classes instead of 1 |
| Extensions | `db` (via `get_sqla_class`), `appbuilder` | Add Flask-Migrate, session/project context helpers |
| Models | Single `Project` model (code, name, description) + `AuditMixin` | Full domain model (~18 tables) |
| Views | One `ProjectView` (ModelView) | Full view catalogue at Section 8 |
| Auth | `AUTH_DB`, CSRF on | Needs role model for RBAC (Admin exists; add domain roles) |
| UI theme | Bootswatch `slate` | Retain; add project-scoped navbar and custom templates |
| Tests | 8 passing pytest tests (in-memory SQLite) | Extend per module (Section 17) |
| Schema mgmt | `Model.metadata.create_all` at boot | Move to Flask-Migrate (Alembic) |

---

## 3. Guiding Principles

1. **Project-scoped everything.** Artefacts, modules, baselines and reviews are
   meaningless outside a project context. The UI is built around a *current project*
   switcher that drives row-level filters on every project-scoped view — matching how
   DOORS NG partitions by project/module and keeping list views fast and focused.
2. **CRUD first, views second.** Every entity gets a F.A.B. `ModelView`; custom
   screens (`BaseView`) are added only where the built-in behaviour cannot express the
   workflow (trace graph, diff, impact analysis, ordered module editor).
3. **Auditable by default.** `AuditMixin` on mutable entities + an immutable
   `ArtefactVersion` snapshot on every artefact save.
4. **Status as workflow data, not code.** Lifecycle states are enumerated columns
   driven by role+state transition rules via F.A.B. actions and `pre_update` hooks.
5. **External services behind interfaces.** OCR, embeddings, LLM suggestions and
   semantic search are consumed through thin service interfaces so the core app is
   testable offline.
6. **Naming convention.** Every registered view class name must be unique, and all
   registration lives in one idempotent `register_views()` helper (Section 8.1) whose
   guard keys off the always-present `"ArtefactView"`. Renaming a class or menu entry
   is safe because registration ends with `appbuilder.security_cleanup()`, which prunes
   the orphaned permissions.

---

## 4. Technology Stack and New Dependencies

**Runtime (add to `pyproject.toml` `dependencies`):**

| Package | Purpose |
|---|---|
| `flask-appbuilder>=5.2` | Existing — view framework |
| `flask-migrate` | Alembic schema migrations (replaces `create_all`) |
| `apscheduler` | Scheduled background jobs (suspect-link rechecks, report generation) |
| `flask-ckeditor>=0.4` | Rich-text editor on artefact content (FR-2/FR-4) |
| `bleach` | Sanitise rich-text HTML before DB write and render |
| `openpyxl` | Excel trace matrices / exports (FR-10) |
| `python-docx` | Word report export (FR-10) |
| `weasyprint` | PDF export (FR-10) |
| `Pillow` | Image/media handling for attachments and OCR pre-processing (FR-11) |
| `sqlalchemy-utils` | `JSONType`, `UUIDType`, `EmailType` (already transitive; make direct) |
| `psycopg[binary]` (optional) | Production Postgres driver (NFR scalability) |
| `authlib` (optional, later) | OAuth/SSO (NFR; F.A.B. AUTH_OAUTH) |

**Dev (`dependency-groups.dev`):**
| Package | Purpose |
|---|---|
| `pytest`, `pytest-cov` | Test runner + coverage |
| `types-*` / `ruff` (optional) | Linting/types |

**Implementation order note:** add `flask-migrate`, `bleach`, `Pillow` and
`sqlalchemy-utils` first; add filesystem-era deps (`openpyxl`, `python-docx`,
`weasyprint`) in the reporting phase and AI deps only when providers are chosen.

---

## 5. Target Application Structure

```
app/
├── __init__.py                # factory: init db, migrate, register_views()
├── extensions.py              # db, appbuilder, migrate, project-context helpers
├── config.py                  # moved to project-level config (unchanged location)
├── models/                    # split from single models.py (keeps audit + mixins)
│   ├── __init__.py            # re-export all; central db.Model metadata registry
│   ├── mixins.py              # StatusMixin, SoftDeleteMixin, TagMixin
│   ├── project.py             # Project, ProjectTemplate, ProjectTag
│   ├── artefact.py            # ArtefactType, Artefact, ArtefactVersion, Attachment, Tag
│   ├── relationship.py        # RelationshipType, ArtefactRelationship
│   ├── module.py              # Folder, Module, ModuleEntry
│   ├── baseline.py            # Baseline, BaselineEntry
│   ├── review.py              # Review, ReviewAssignment, Comment, Approval
│   ├── change.py              # ChangeRequest, ChangeSetItem
│   ├── search.py              # SavedQuery
│   └── media.py               # MediaArtefact, MediaExtractionResult
├── views/
│   ├── __init__.py            # register_views() — single registration point
│   ├── project_views.py       # ProjectView, ProjectTemplateView
│   ├── artefact_views.py      # ArtefactTypeView, ArtefactView, AttachmentView
│   ├── relationship_views.py  # RelationshipTypeView, RelationshipView, TraceGraphView
│   ├── module_views.py        # ModuleView, ModuleContentView (template)
│   ├── baseline_views.py      # BaselineView, BaselineBuildForm, BaselineCompareView
│   ├── review_views.py        # ReviewView, CommentView, ReviewReportView
│   ├── change_views.py        # ChangeRequestView
│   ├── search_views.py        # SearchView
│   ├── report_views.py        # TraceMatrixView, CoveragereportView
│   ├── dashboard_views.py     # HomeView (custom IndexView) + chart views
│   └── api.py                 # ModelRestApi + BaseApi classes
├── services/                  # business logic, testable without HTTP
│   ├── versioning.py          # create_version_on_save(), restore()
│   ├── relationship_service.py# validate + create links, suspect detection
│   ├── baseline_service.py    # snapshot(), compare(), restore()
│   ├── impact.py              # forward/reverse traversal, orphan detection
│   ├── workflow.py            # transition table + guards
│   ├── reporting.py           # trace matrix / coverage / word/pdf/excel
│   ├── project_service.py     # clone, archive, template-from-project, import/export
│   └── ai_provider.py         # abstract provider: duplicate, suggest, summarise
├── templates/
│   ├── base.html              # extends appbuilder/base.html; adds project switcher
│   ├── project_switcher.html  # navbar partial (session['project_id'])
│   ├── artefact_show.html     # rich content + relationship tabs
│   ├── trace_graph.html       # vis.js network; impact/suspect modes
│   ├── impact.html            # impacted artefact table
│   ├── compare.html           # version/baseline diff
│   ├── module_content.html    # ordered editor + drag-drop
│   ├── dashboard.html         # KPI cards + charts
│   ├── review_report.html     # review report print view
│   └── search_results.html
└── static/
    └── js/vis.min.js, cytoscape.min.js   # vendored graph lib
```

A tiny seed CLI (`flask fab` style) and `wsgi.py` complete the deployment story.

---

## 6. Configuration Changes

In `config.py` (or env-driven overrides):

```python
# Now mandatory
SQLALCHEMY_DATABASE_URI = os.getenv("REQDOOR_DB", "sqlite:///" + os.path.join(basedir, "app.db"))
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Project context session key
PROJECT_SESSION_KEY = "reqdoor_project_id"

# Workflow engine config
WORKFLOW_REQUIREMENT_LIFECYCLE = [...Draft...Released...Obsolete...]

# Media / AI
MEDIA_UPLOAD_FOLDER = basedir + "/app/static/uploads/"
MEDIA_ANALYSIS_PROVIDER = "none"          # "tesseract" | "external_api"
AI_PROVIDER = "none"                      # "openai" | "ollama" | ...
AI_PROVIDER_CONFIG = {...}
SEARCH_ENGINE = "sqlite_fts"               # upgrade phase: "postgres_tsvector" | "vector"
```

Also set `AUTH_USER_REGISTRATION = False` (or keep Public) and define domain roles via
`FAB_ROLES` or seeded users in a `bootstrap.py` run through `flask fab` hook.

---

## 7. Data Model Design

All models inherit `flask_appbuilder.Model` (same declarative namespace as security
tables) and must define explicit `__tablename__` (F.A.B. 5.x requirement). Mutable
entities add `AuditMixin`. All numeric IDs are `Integer` auto-increment primary keys
(SQLite-friendly in dev; Postgres in prod). A single global `global_identifier`
(UUID string) is stored per artefact and version for DOORS-like stable identity.

### 7.1 Core model catalogue

| Table | Entity | Key columns | Notes |
|---|---|---|---|
| `project` | Project | `project_code (unique)`, `name`, `description`, `owner_id→ab_user`, `lifecycle_state`, `is_archived`**, `tags` | Extend existing model |
| `project_template` | Template | `name`, `description`, `artefact_type_defaults (JSON)`, `module_defaults (JSON)` | FR-1 templates |
| `project_tag` / `tag` | Tags | `name`, `color` | M2M to projects/artefacts |
| `artefact_type` | Type definition | `name (unique)`, `display_name`, `description`, `schema_definition (JSON)`, `mandatory_fields (JSON)`, `workflow_config (JSON)` | FR-3 |
| `artefact` | Artefact | `project_id`, `artefact_type_id`, `global_identifier (unique uuid)`, `title`, `content_html (Text)`, `status`, `priority`, `owner_id→ab_user`, `category`, `tags`, `custom_attributes (JSON)`, `is_deleted` | FR-2 |
| `artefact_version` | Immutable snapshot | `artefact_id`, `version_number (int, per-artefact seq)`, `snapshot (JSON)`, `change_summary`, `author_id→ab_user`, `content_html` | FR-4, FR-6 |
| `attachment` | File evidence | `artefact_id`, `file (FileColumn)`, `original_name`, `media_type` | FR-2 |
| `relationship_type` | Link type def | `name (unique)`, `display_name`, `is_directional`, `source_type_ok (JSON)`, `target_type_ok (JSON)`, `rules (JSON)` | FR-5 |
| `artefact_relationship` | Trace link | `project_id`, `source_id→artefact`, `target_id→artefact`, `relationship_type_id`, `is_suspect` | FR-5; unique(source,target,type) |
| `folder` | Containment | `project_id`, `parent_id→folder`, `name`, `position` | precedence lower |
| `module` | Ordered document | `project_id`, `folder_id`, `name`, `description` | FR-2 collections |
| `module_entry` | Ordered artefact ref | `module_id`, `artefact_id`, `position`, `custom_number` | hierarchical numbering |
| `baseline` | Configuration snapshot | `project_id`, `name`, `description`, `version_number` | FR-6 |
| `baseline_entry` | Version-in-baseline | `baseline_id`, `artefact_id`, `artefact_version_id`, `module_id→nullable`, `position` | immutable evidence |
| `review` | Formal review | `project_id`, `title`, `status`, `due_date`, `target_type (enum)`, `target_id` | FR-7 |
| `review_assignment` | Reviewer role | `review_id`, `user_id→ab_user`, `role_in_review`, `response_status` | FR-7 |
| `comment` | Threaded discussion | `review_id (nullable)`, `artefact_id (nullable)`, `parent_id→comment`, `body`, `resolved` | FR-7 |
| `approval` | E-sign record | `review_id`, `user_id`, `decision (approve/reject)`, `signature_text`, `date` | FR-7 |
| `change_request` | Change control | `project_id`, `title`, `description`, `status`, `priority`, `requested_by` | FR-8 |
| `change_set_item` | Modification set | `change_request_id`, `artefact_id`, `action (add/update/delete)`, `proposed_snapshot (JSON)`, `applied` | FR-8 |
| `saved_query` | Persisted search | `name`, `project_id`, `owner_id`, `query_params (JSON)` | FR-9 |
| `media_artefact` | Media evidence | `project_id`, `title`, `media_file (FileColumn)`, `media_type`, `ai_extracted_content (JSON)` | FR-11 |
| `media_extraction_result` | AI candidates | `media_artefact_id`, `kind (ocr/candidate/object)`, `payload (JSON)`, `source_artefact_id→nullable`, `status (pending/validated/rejected)` | FR-11/12 |

### 7.2 Relationship and referential rules

- `artefact_relationship.project_id` duplicates for query locality and to allow
  project-scoped filters without joins.
- `ArtefactVersion` is written **only** by the versioning service (Section 8.3) to keep
  snapshots consistent; never via the ModelView add form directly.
- `comment` is polymorphic (review *or* artefact target) so the UI can serve both
  Review-detail threaded comments (FR-7) and artefact-level discussion (UC).
- `module_entry` ordering is the single source for module hierarchy and numbering;
  `position` is a decimal ("Anderson ordering") to support drag-drop without renumbering
  all siblings.
- Deleting an artefact is a **soft delete** (`is_deleted`) when it appears in a
  baseline or has relationships, preserving traceability (FR-6, FR-5).

---

## 8. Flask-AppBuilder View Architecture and UI Workflow Design

This is the heart of the plan. The UI is organised so that a user can move fluently
between *Project → Module → Artefact → Relationship/Version/Review* screens while
keeping the current project fixed.

### 8.1 Single registration point

Replace the one-off `appbuilder.add_view(...)` in `create_app()` with a guarded helper
so all views are registered once per process (required by the current idempotency
guard and by F.A.B. menu building):

```python
# app/views/__init__.py
def register_views(appbuilder):
    """Register all menus/views exactly once (called from create_app)."""
    if appbuilder.get_session:  # already initialized by init_app
        pass
    registered = {v.__class__.__name__: True for v in appbuilder.baseviews}
    if registered.get("ArtefactView"):
        return                     # create_app called more than once (tests)

    from .project_views import ProjectView, ProjectTemplateView
    from .artefact_views import (ArtefactTypeView, ArtefactView, AttachmentView)
    from .relationship_views import (RelationshipTypeView, RelationshipView,
                                     TraceGraphView, ImpactAnalysisView)
    from .module_views import ModuleView, ModuleContentView
    from .baseline_views import BaselineView, BaselineCompareView
    from .review_views import ReviewView, CommentView, ReviewReportView
    from .change_views import ChangeRequestView
    from .search_views import SearchView
    from .report_views import TraceMatrixView
    from .dashboard_views import HomeView
    from .api import ProjectApi, ArtefactApi, RelationshipApi

    # Menus (categories below)
    appbuilder.add_view_no_menu(HomeView)             # set as index
    appbuilder.add_view(ProjectView, "Projects", icon="fa-folder-open-o", category="Requirements")
    appbuilder.add_view(ProjectTemplateView, "Templates", icon="fa-copy", category="Management")
    appbuilder.add_view(ArtefactTypeView, "Artefact Types", icon="fa-th-large", category="Administration")
    appbuilder.add_view(RelationshipTypeView, "Link Types", icon="fa-sitemap", category="Administration")
    appbuilder.add_view(ArtefactView, "Artefacts", icon="fa-file-text-o", category="Requirements")
    appbuilder.add_view(ModuleView, "Modules", icon="fa-book", category="Requirements")
    appbuilder.add_view(RelationshipView, "Trace Links", icon="fa-code-fork", category="Requirements")
    appbuilder.add_view(ModuleContentView, "Module Content", icon="fa-list", category="Requirements")
    appbuilder.add_view(SearchView, "Search", icon="fa-search", category="Requirements")
    appbuilder.add_view(TraceGraphView, "Traceability", icon="fa-share-alt", category="Analysis")
    appbuilder.add_view(ImpactAnalysisView, "Impact Analysis", icon="fa-sitemap", category="Analysis")
    appbuilder.add_view(BaselineView, "Baselines", icon="fa-lock", category="Quality")
    appbuilder.add_view(ReviewView, "Reviews", icon="fa-check-square-o", category="Quality")
    appbuilder.add_view(ChangeRequestView, "Change Requests", icon="fa-pencil-square-o", category="Quality")
    appbuilder.add_view(TraceMatrixView, "Trace Matrix", icon="fa-table", category="Reporting")
```
> The guard keys on `"ArtefactView"` (as in the shipped `register_views()`);
> a module-level flag would work just as well.

### 8.2 Navigation and menu design

**Top-level Category structure** (mirrors DOORS NG task grouping):

| Category | Menu items | Why grouped here |
|---|---|---|
| **(Home)** | Dashboard | Entry dashboard with KPI cards (no menu entry) |
| **Management** | Projects, Project Templates | Project administration |
| **Requirements** | Artefacts, Modules, Module Content, Trace Links, Search | Hands-on RM work |
| **Analysis** | Traceability, Impact Analysis | Engineering analysis |
| **Quality** | Reviews, Baselines, Change Requests | Governance gates |
| **Reporting** | Trace Matrix | Compliance exports |
| **Administration** | Artefact Types, Link Types, (F.A.B. Admin: Users/Roles) | Configuration |
| **Security** | (F.A.B. built-in login/menu) | Auth |

> **Shipped today (PoC):** the menu collapses to the loop order
> **Requirements** (Projects, Artefacts, Trace Links, *Draft artefacts*) →
> **Governance** (Baselines, Reviews, *Active reviews*, Change Requests,
> *Open change requests*) → **Analysis** (Traceability Graph, Impact Report,
> *Suspect trace links*, Orphan report) → **Dashboard** (Artefact Mix, Artefacts
> per Project) → **Administration** (Link Types), with indented entries being
> saved-filter links. Modules/Search/Reporting categories join as their views land.

**Project context switcher** — a dropdown in the right navbar (custom partial
`project_switcher.html`) that posts to `ProjectContextApi.switch`, storing
`session[PROJECT_SESSION_KEY]`. Every project-scoped view then filters through
`base_filters`:

```python
from flask_appbuilder.models.sqla.filters import FilterEqualFunction
def project_of(g):
    return g.get("reqdoor_project_id")
class ScopedProjectView(ModelView):
    base_filters = [["project", FilterEqualFunction, project_of]]
```
> `ArtefactTypeView` / `RelationshipTypeView` are **not** project-scoped (they are
> configuration shared across projects). Dashboard and Reporting views may be global.

### 8.3 Shared view helpers and services used by views

```
app/services/workflow.py     transition(action, current_state, user) -> allowed/raises
app/services/versioning.py   snapshot_artefact(artefact, summary) ; restore(version)
app/services/relationship_service.py  create_relationship(...) -> validates types/rules,
                                     detect_suspect(source) -> flags dependents
app/services/baseline_service.py      snapshot_project(project) -> Baseline+entries
app/services/impact.py                forward_impact(ids), reverse_impact(ids), orphans()
```

Every view that mutates an artefact calls the service (not raw `datamodel.add`) so
version snapshots and suspect flags are never bypassed by a form POST.
`ModelView.pre_add / pre_update / pre_delete` hooks are the enforcement points.

### 8.4 Per-screen UI designs

#### Screen 1 — Dashboard (HomeView : IndexView)
- **Class:** `HomeView(IndexView)`, `index_template='dashboard.html'`, registered via
  `appbuilder.add_view_no_menu` + passed as `indexview=` to `AppBuilder`.
- **Contents:** KPI cards (open artefacts, by status, open reviews, open change
  requests, due baselines), 2 chart views (`GroupByChartView` on `Artefact.status`, and
  one on `ArtefactType`), recent-change feed (last 10 versions).
- **Workflow role:** task triage landing page.

#### Screen 2 — Projects list (ProjectView : ModelView)
- **List columns:** `project_code`, `name`, `owner`, `lifecycle_state`, `is_archived`,
  `changed_on`. Search on code/name/owner.
- **Fieldsets (add/edit):** *Project Details* (code, name, description, owner) and
  *Governance* (lifecycle_state, is_archived, tags).
- **Actions:** `@action('archive')` (sets `is_archived`; confirmed), `@action('clone')`
  (calls `project_service.clone` → JSON import of type/folder/module definitions and
  artefacts), `@action('export')`/`import` (download/upload JSON).
- **Related views (project detail tabs):** `ArtefactView` (scoped), `ModuleView`,
  `BaselineView`, `ReviewView`, `ChangeRequestView`.
- **Workflow role:** enter project context by clicking through — show page has a
  prominent **"Set as current project"** button (posts to context API) so all
  Requirements menu entries are automatically filtered.

#### Screen 3 — Artefact Type admin (ArtefactTypeView : ModelView)
- Read-mostly; add/edit form with a **JSON schema textarea validator** (`bleach` not
  needed here). `mandatory_fields` used to drive artefact add form accordingly.
- **Workflow role:** configuration; low traffic; expected only by admins.

#### Screen 4 — Artefact list & editor (ArtefactView : ModelView) — **central screen**
- **List:** `[global_identifier]`, `title`, `artefact_type`, `status`, `priority`,
  `owner`, `module` (via dotted), `changed_on`. Filters: type, status, priority,
  module, owner, tags, full-text search (Section 13).
- **Add/Edit form:** title, type (drives dynamic custom-attribute fields + mandatory
  list), status (validated by workflow), owner, priority, tags, and rich-text content
  via `flask-ckeditor` (sanitised with `bleach` before save).
- **Show page (custom `artefact_show.html`), tabbed via `related_views`:**
  - *Content* — sanitised HTML render.
  - *Versions* — `ArtefactVersionView` (read-only list, show-only columns), with a
    `@action('restore')` (writes a new version, does not delete).
  - *Incoming links* / *Outgoing links* — two `RelationshipView`-derived related views
    (`base_filters` on target/source), with a **"Create Link"** button.
  - *Comments* — `CommentView` (threaded, artefact target).
- **Bulk toolbar:** `@action('bulk_edit', single=False)` opens a stashed-form to batch
  set status/priority/owner on selected rows (FR-2 bulk modify).
- **Workflow role:** the main authoring screen; UC-01 lands here.

#### Screen 5 — Modules (ModuleView : ModelView) + Module Content editor (template)
- **ModuleView list:** name, folder, artefact count, changed_on. Detail tabs include
  an ordered list of module entries (via `related_views` on `ModuleEntryView`).
- **ModuleContentView (BaseView + `module_content.html`):** table of
  section→artefact rows with drag-and-drop ordering (posts position updates to a
  module API), hierarchical numbering computed from tree position, and an "Add
  Artefact" picker (select2). This delivers FR-2's ordered document and numbering
  capability in a purpose-built screen rather than fighting the CRUD list widget.

#### Screen 6 — Trace links (RelationshipView : ModelView)
- **List:** source, target (auto labels via `__repr__`), relationship type,
  direction, `is_suspect` badge.
- **Add form:** `SimpleFormView` (`LinkFormView`) is used for the primary UX — a form
  with **source (freeze if opened from an artefact)**, **target (select2 searchable)**,
  and **type dropdown**, then `relationship_service.create_relationship` validates
  source/target type compatibility and duplicate rules (FR-5).
- **Actions:** `@action('remove')`, `@action('mark_valid')` (clears suspect flag).
- **Workflow role:** UC-02.

#### Screen 7 — Traceability graph (TraceGraphView : BaseView)
- Route: `/tracegraphview/show/<artefact_id>`; template `trace_graph.html` with
  **vis.js network**; nodes = artefacts coloured by type/status; edges labelled by
  link type, dashed when `is_suspect`.
- **Reused by Impact Analysis** (`impact.html`) with switchable modes:
  - *Forward impact* (what depends on changed artefact) and *Reverse* (what it
    depends on) — via `impact.py`.
  - *Suspect links* — dependents of any artefact changed since last baseline.
  - *Orphan detection* — artefacts with zero relationships (report list).
- **Workflow role:** UC-04 and FR-5 visualisation; "Export graph" PNG via canvas.

#### Screen 8 — Baselines (BaselineView : ModelView + BaseViews)
- **List:** name, version, date, owner, artefact count.
- **"Build Baseline"** button → `BaselineBuildForm (SimpleFormView)`: name, description;
  `baseline_service.snapshot_project` copies current versions into `baseline_entry`
  (FR-6 regulatory evidence). Snapshots are **read-only** (no delete action exposed;
  `exclude_route_methods={"delete"}`).
- **CompareView (BaseView, `compare.html`):** select two baselines → diff table
  (added/removed/changed artefacts + version deltas).
- **Audit contents:** `BaselineEntryView` related view → shows artefact + version +
  module position at snapshot time.
- **Restore:** `@action('restore')` writes new artefact versions from baseline entries
  (never deletes) — FR-6 historical restoration without breaking traceability.
- **Workflow role:** UC-03, UC-07.

#### Screen 9 — Reviews (ReviewView : ModelView + related views)
- **List:** title, status (Planned→Active→Comment Resolution→Approved→Closed), due
  date, decision ratio.
- **Detail tabs (related_views):**
  - *Assignments* — `ReviewAssignmentView` (add reviewer, set role).
  - *Comments* — `CommentView` (threaded, resolve controls).
  - *Approvals* — `ApprovalView` (add → decision + e-signature text captured from the
    logged-in user).
  - *Artefacts under review* — via target module/baseline filtering.
- **Actions:** `@action('activate')`, `@action('approve')`, `@action('reject')`,
  `@action('close')` — all routed through `workflow.py` guards.
- **ReviewReportView (BaseView):** printable report (comments, approvals, outstanding
  items) — FR-7.

#### Screen 10 — Change requests (ChangeRequestView : ModelView)
- Fields: title, description, status (Submitted→Analysed→Approved→Implemented→
  Verified→Closed), priority, requested_by.
- **Related:** `ChangeSetItemView` (add/modify artefacts with proposed snapshot,
  action typing). Impact link: **"Run Impact Analysis"** button posts selected
  artefacts to `ImpactAnalysisView` (UC-04).
- **Actions:** tunnel through `workflow.py`; applying a change set calls the versioning
  service so each applied edit is versioned (FR-8).

#### Screen 11 — Search (SearchView : BaseView)
- Landing form: keyword, scope (project, module, type, status), plus saved query list
  (`SavedQueryView`). Results rendered in a F.A.B. list-like table with links to
  `ArtefactView.show`. FR-9 SA terms: full-text engine per Section 13.

#### Screen 12 — Reporting (TraceMatrixView / CoveragereportView : BaseView)
- Trace matrix: rows = source artefacts (or requirements), columns = target types
  (e.g., Requirement→Test Case), cell = link count; export XLSX (`openpyxl`),
  Word (`python-docx`), PDF (`weasyprint`) streamed as `send_file`/`Response` with
  content-disposition. Coverage report computes requirement→test coverage gaps
  (UC-07). FR-10.

### 8.5 Cross-cutting UI workflow concerns

- **Redirect hygiene:** use `self.update_redirect()` / `self.get_redirect()` on
  custom `BaseView` actions so the user returns to the screen they came from.
- **Confirmation dialogs:** every destructive action (`@action`) passes a
  `confirmation=` string; bulk-delete communicates impact count (incl. dependents).
- **Empty states:** each ModelView sets useful `label_columns`/`description_columns`;
  custom templates render guidance when a zero-row list appears (e.g., "Create your
  first artefact").
- **i18n:** every menu label and form label wrapped in `lazy_gettext` (existing
  LANGUAGES block), keeping the slate theme applied consistently.
- **Validation UX:** `validators_columns` on artefacts (required title, unique
  identifier, parseable JSON on types) renders inline F.A.B. error blocks.

---

## 9. Functional Requirement Implementation Matrix

| FR | Delivered by (models + views + services) | Phase |
|---|---|---|
| FR-1 Project Mgmt | `project` (extended), `project_template`; `ProjectView` actions (archive/clone/export/import), RBAC `base_filters` | 1 |
| FR-2 Artefact Mgmt | `artefact`, `artefact_type`, `attachment`, `module`/`module_entry`; `ArtefactView` (CKEditor, dynamic fields, bulk action, attachments) | 1 |
| FR-3 Type Admin | `artefact_type`, `relationship_type`; `ArtefactTypeView`, `RelationshipTypeView` (JSON schema + validator) | 1 |
| FR-4 Version Mgmt | `artefact_version`; versioning service; show tabs, `@action('restore')`, compare view | 1 |
| FR-5 Traceability | `artefact_relationship`, `relationship_type`; `RelationshipView`, `LinkFormView`, `TraceGraphView`, `ImpactAnalysisView` (suspect + orphans) | 2 |
| FR-6 Baseline Mgmt | `baseline`, `baseline_entry`; `BaselineBuildForm`, `BaselineCompareView`, restore action | 2 |
| FR-7 Review Mgmt | `review`, `review_assignment`, `comment`, `approval`; `ReviewView` tabs + `ReviewReportView` | 2 |
| FR-8 Change Mgmt | `change_request`, `change_set_item`; `ChangeRequestView` + impact button | 2 |
| FR-9 Search/Analysis | `saved_query`; `SearchView`, dashboard charts; FTS engine (Section 13) | 3 |
| FR-10 Reporting | reporting service; `TraceMatrixView`, coverage view; PDF/Word/Excel export | 3 |
| FR-11 Media Analysis | `media_artefact`, `media_extraction_result`; `MediaArtefactView`, analysis provider service | 4 |
| FR-12 AI Assistance | `ai_provider` interface; candidate/dedup/summary screens behind provider | 4–5 |

**NFR mapping (Section 15):**
| NFR | Approach |
|---|---|
| Search < 2 s | Indexed FTS + small page sizes + Postgres `tsvector` in prod (Section 13) |
| 1M+ artefacts | Partition queries by project (`base_filters`), index strategy, streaming reports |
| 99.9% / SSO / MFA / audit | Production deploy (gunicorn+Postgres), F.A.B. `AUTH_OAUTH` (Authlib) + IdP MFA, `AuditMixin` + immutable versions |
| Compliance (ISO 26262 etc.) | Baselines + immutable versions + audit fields + signed approvals map to the common evidence model; reporting views generate the artefacts needed for each standard |

---

## 10. Use Case to Screen-Flow Mappings

| Use case | Primary actor | Screen flow |
|---|---|---|
| UC-01 Create Requirement | Requirements Engineer | Set project context → Requirements▸Artefacts ▸ Add ▸ pick type/attributes ▸ save (version auto-created) |
| UC-02 Create Trace Link | Systems Engineer | Open artefact ▸ "Create Link" ▸ choose direction/target/type ▸ validate ▸ save; view in Traceability |
| UC-03 Build Baseline | Config Manager | Quality▸Baselines ▸ "Build Baseline" ▸ name/description ▸ release (locked) |
| UC-04 Impact Analysis | Change Manager | From artefact or change request ▸ "Impact Analysis" ▸ graph/table of impacted ▸ export report |
| UC-05 Conduct Review | Reviewer | Quality▸Reviews ▸ open ▸ comment thread ▸ approve/reject ▸ e-signature |
| UC-06 Extract Req from Video | Business Analyst | Requirements▸Media (Phase 4) ▸ upload ▸ analysis ▸ validate candidates ▸ promote to artefacts |
| UC-07 Verify Coverage | Test Manager | Reporting▸Trace Matrix / Coverage ▸ select baseline ▸ matrix ▸ export |

---

## 11. Security and Role-Based Access

- **Authentication:** retain `AUTH_DB`; later switch to `AUTH_OAUTH` (SSO) per NFR —
  F.A.B. `OAUTH_PROVIDERS`; MFA is delegated to the IdP (no in-app TOTP in MVP).
- **Roles (seed at bootstrap, assign per user):**
  | Role | Capabilities |
  |---|---|
  | `Admin` (built-in) | Everything incl. type administration |
  | `Project Manager` | Create/archive/clone projects, baselines, reviews |
  | `Requirements Eng.` | Full artefact + module + link CRUD in their project |
  | `Reviewer` | Read artefacts, comment, approve/reject in assigned reviews |
  | `Auditor` | Read-only + baselines + reports (`base_permissions=["can_list","can_show"]`) |
  | `ReadOnly` | Built-in F.A.B. read-only role using regex permissions |
- **Enforcement:** F.A.B. permission granularity (`can_* on View`) + `base_filters`
  row-level scoping by project for domain views; custom `@has_access` on BaseViews.
- **Audit:** `AuditMixin` everywhere + immutable `artefact_version`; export of audit
  rows for compliance.

---

## 12. API Layer

- **ModelRestApi classes** for programmatic/enablement integrations:
  `ProjectApi`, `ArtefactApi`, `RelationshipApi`, `BaselineApi`, `ReviewApi`,
  `ChangeRequestApi` — resource names under `/api/v1/`, JWT-secured
  (`appbuilder.add_api`), Rison filters. Meta-data keys give frontends the authored
  fields, validators on marshmallow schemas (422 UX).
- **BaseApi** `ProjectContextApi` (`/api/v1/projectcontext/<id>`, sets session),
  and `ModuleReorderApi` (positions), `MediaAnalysisApi` (async analysis job
  trigger/status).
- API mirrors `services/` so CRUD through UI or API takes identical code paths
  (versions, suspect flags, workflow guards).

---

## 13. Search, Reporting and Analytics

**Search engine (phased):**
- **Phase 1 (MVP):** F.A.B. built-in `search_columns` on list views (LIKE) + simple
  `SearchView` across title/content using SQLite `LIKE` with `ILIKE` fallback.
- **Phase 2:** SQLite **FTS5** virtual table fed by the versioning service, kept in
  sync via triggers/service (meets typical in-app search latency).
- **Phase 3 (prod):** Postgres `tsvector` with GIN index; optional vector embeddings
  behind `ai_provider` for semantic top-k retrieval (FR-9 "semantic search").
- **Saved queries:** `saved_query` model + `SavedQueryView`; one-click re-run.

**Analytics dashboards (FR-9 analytics):**
- `GroupByChartView` on status/type/priority; KPI cards on Home; XML/JSON export for
  downstream BI. Volatility & change-velocity metrics computed by reporting service
  from `artefact_version` history (FR-9, §9 reporting list).

---

## 14. Media Analysis and AI Assistance

Both are **Phase 4–5** and isolated behind interfaces so the core app ships without
them:

- `MediaArtefactView` (ModelView) with `FileColumn` upload + `@action('analyse')`.
- `MediaAnalysis` service: OCR via Tesseract (wrapper) or external vision API;
  results stored in `media_extraction_result` with `status='pending'`; validation
  screen promotes a candidate to an `Artefact` and auto-links it (UC-06).
- `ai_provider.py`: abstract methods `suggest(summary, context)`,
  `detect_duplicates(title, project)`, `recommend_links(artefact)` ,
  `summarise(artefact|baseline)`; a `NoneProvider` no-op keeps UI enabled;
  pluggable OpenAI/Ollama implementations behind `AI_PROVIDER` config.
- AI is always **assisted**: candidates are shown for human validation, never
  auto-written to production artefacts.

---

## 15. Non-Functional Requirements Strategy

- **Performance:** project-scoped indexing; composite indexes on
  `(project_id, artefact_type_id)`, `(source_id)`, `(target_id)`, module position;
  report queries streamed; list `page_size` tuned.
- **Scalability:** move to Postgres at deployment milestone; keep SQLite for dev/tests.
- **Availability:** stateless app factory + gunicorn workers; scheduled jobs via
  APScheduler (can move to Celery later).
- **Security:** CSRF on, bleach sanitisation on all rich text, `FAB_PASSWORD_*`
  complexity on, secret from env, HTTPS at LB, OAuth SSO + IdP MFA in Phase 5.
- **Compliance evidence model:** the baseline+version+approval triad is the single
  audit backbone reused for DO-178C, ISO 26262, IEC 61508, ASPICE artifacts and
  reports (Section 9 NFR row).

---

## 16. Schema Management and Migrations

- Replace `Model.metadata.create_all` in `create_app` with `flask db upgrade` flow:
  - `app/extensions.py` adds `from flask_migrate import Migrate; migrate = Migrate()`.
  - `create_app()` calls `migrate.init_app(app, db)`.
  - `FAB_CREATE_DB = False` in config; migrations owned in `migrations/` (Alembic).
  - Initial migration seeds security tables via F.A.B. at first `flask fab create-admin`.
- Tests keep in-memory SQLite but still run `db.create_all()` in the fixture (no
  migration overhead); CI can run `flask db upgrade` against Postgres if configured.

---

## 17. Testing Strategy

Layered on the existing 8 passing tests (`tests/conftest.py` in-memory pattern):

| Layer | Approach |
|---|---|
| Unit (services) | Pure pytest on `services/{versioning, relationship_service, impact, baseline_service, workflow}.py` — no HTTP |
| View/integration | `test_app.py`, `test_views.py` pattern per view: login via `/login/`, assert CRUD round-trips, action buttons, related tabs presence |
| Workflow | matrix tests: every allowed/denied transition for each role |
| Report/AI | golden-file assertions on matrix output; `NoneProvider`/mock provider for AI paths |
| API | JWT login → Rison list/add/validation-error (422) assertions on `ModelRestApi` |

Coverage gate ≥ 70% on `app/services` initially; views covered smoke-level.

---

## 18. Phased Delivery Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **Phase 0 — Foundation** | Migrations (Flask-Migrate), `register_views()` refactor, project-context switcher, services skeleton, bootstrap roles | app boots with menus; existing 8 tests green |
| **Phase 1 — Core RM** | Project features (FR-1), Artefact/Type/Module/Version (FR-2/3/4) | Artefact authoring, versioning, module editor working end-to-end |
| **Phase 2 — Traceability & Governance** | Relationships + graph + suspect/orphans (FR-5), Baselines (FR-6), Reviews (FR-7), Change (FR-8) | UC-02/03/04/05 usable |
| **Phase 3 — Search & Reporting** | Full-text search + saved queries (FR-9), dashboards, trace matrix + exports (FR-10) | UC-07; PDF/XLSX/Word downloads |
| **Phase 4 — Media & AI-assisted** | Media artefacts/OCR/validation (FR-11), duplicate detection + suggestions (FR-12 subset) | UC-06 demo; AI offline tests |
| **Phase 5 — Enterprise hardening** | SSO/OAuth, MFA (IdP), Postgres scale, performance tuning, compliance report packs, semantic search | NFR checks pass; perf test on 1M row fixture |

> Phases 1–3 are the priority for a working RM tool; 4–5 are capability extensions
> that slot into the same navigation without UI rework.

---

## 19. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| F.A.B. global `appbuilder` singleton makes multi-app/test setup fragile | Guarded idempotent `register_views()` (existing pattern), session-scoped app fixture |
| Versioning bypassed by a form POST | All artefact mutation through `services/versioning` hooks in `pre_*`; API mirrors it |
| Rich-text XSS | `bleach` sanitise on write; `Markup` only with sanitised content on render |
| CRUD list unfit for ordered modules / graph | Purpose-built `BaseView` templates (ModuleContent, Trace, Compare) — never force them into list widgets |
| 1M artefacts on SQLite | Postgres at deploy milestone; project-scoped queries everywhere |
| External AI/OCR deps unavailable | Provider interfaces + no-op/mock providers keep every earlier phase fully functional |
| Permission sprawl across 15 views | Consistent role→`base_permissions` map; `security-converge` on renames |

---

## 20. Open Design Decisions

1. **Hierarchical numbering scheme** for module entries (decimal vs continuous
   renumber) — decide in Phase 1 before `module_entry.position` is finalised.
2. **Suspect-link model** (event-driven on publish vs periodic batch) — initial
   design is synchronous recheck on artefact change; may move to APScheduler job.
3. **Rich-text editor choice** (CKEditor via flask-ckeditor vs self-hosted contenteditable)
   — flask-ckeditor chosen for F.A.B. integration speed; re-evaluate for compliance
   (HTML diffing) in Phase 2.
4. **Semantic search embedding store** (pgvector vs external vector DB) — deferred to
   Phase 5, only the retrieval interface is defined now.
5. **Which compliance packs ship first** (ISO 26262 vs DO-178C) — decide from first
   customer/regulator before Phase 5.

---

*This plan supersedes the skeleton in `run.py`/`app` incrementally; Phase 0 begins by
refactoring `app/views/__init__.py` into `register_views()` and introducing
Flask-Migrate, keeping the project runnable at every step.*