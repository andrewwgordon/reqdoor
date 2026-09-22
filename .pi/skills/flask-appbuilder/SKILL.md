---
name: flask-appbuilder
description: Develops web applications with Flask-AppBuilder.
---

# Flask-AppBuilder (F.A.B.)

A simple and rapid application development framework built on top of Flask. It generates
CRUD views, menus, forms, and security tables automatically from SQLAlchemy models.

> Upstream project: https://github.com/dpgaspar/Flask-AppBuilder
> Full docs: https://flask-appbuilder.readthedocs.io/en/latest/
> Examples: https://github.com/dpgaspar/Flask-AppBuilder/tree/master/examples
> Live demo: http://flaskappbuilder.pythonanywhere.com/ (login: `guest` / `welcome`)

## Quick Reference (TL;DR)

```bash
pip install flask-appbuilder Pillow      # Pillow only needed for image upload
flask fab create-app                      # downloads skeleton app (needs internet)
cd <your_app>
export FLASK_APP=app
flask fab create-admin                    # required since 1.3.0 — no admin is auto-created
flask run                                 # dev server on http://localhost:8080
```

Minimal app:

```python
import os
from flask import Flask
from flask_appbuilder import SQLA, AppBuilder

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "app.db")
app.config["CSRF_ENABLED"] = True
app.config["SECRET_KEY"] = "thisismyscretkey"

db = SQLA(app)                          # F.A.B. SQLA: Flask-SQLAlchemy subclass
appbuilder = AppBuilder(app, db.session)

app.run(host="0.0.0.0", port=8080, debug=True)
```

- `SQLA` is a child class of Flask-SQLAlchemy's `SQLAlchemy` that overrides the
  declarative base so your models can relate to F.A.B. security tables (e.g. Users).
- On first run the DB is created with the `Admin` and `Public` roles plus all
  security tables. Table creation can be disabled with `FAB_CREATE_DB = False`.

## Project Structure (skeleton app)

```
<project>/
├── config.py          # all application config (imported via app.config.from_object)
├── run.py             # dev launcher (optional)
└── app/
    ├── __init__.py    # app + db + appbuilder initialization, view registration
    ├── models.py      # SQLAlchemy models
    ├── views.py       # ModelViews, chart views, REST APIs
    └── templates/     # Jinja2 templates (extends "appbuilder/base.html")
```

`app/__init__.py` typically ends with:

```python
appbuilder = AppBuilder(app, db.session)
from app import views  # noqa: E402  (register views AFTER AppBuilder init)
```

## Configuration (config.py)

Load it with `app.config.from_object("config")`. Mandatory keys: `SECRET_KEY`,
`SQLALCHEMY_DATABASE_URI`, `AUTH_TYPE`. Set `SECRET_KEY` to a long random string:
`python -c 'import secrets; print(secrets.token_hex())'`.

| Key | Purpose |
|---|---|
| `AUTH_TYPE` | `AUTH_DB` (1), `AUTH_LDAP` (2), `AUTH_REMOTE_USER` (3), `AUTH_OAUTH` (4), `AUTH_SAML` — import constants from `flask_appbuilder.security.manager` |
| `AUTH_USER_REGISTRATION` | `True` enables self-registration |
| `AUTH_USER_REGISTRATION_ROLE` | Role given to self-registered users (must exist) |
| `AUTH_USER_REGISTRATION_ROLE_JMESPATH` | JMESPath expression for dynamic role assignment (needs `jmespath`), e.g. `contains(['a@x.com'], email) && 'Admin' \|\| 'Viewer'` |
| `AUTH_ROLE_ADMIN` / `AUTH_ROLE_PUBLIC` | Rename builtin admin/public roles |
| `AUTH_ROLES_MAPPING` | Dict mapping LDAP group DNs / OAuth group names to FAB roles |
| `AUTH_ROLES_SYNC_AT_LOGIN` | Replace user roles at every login (LDAP/OAuth/SAML) |
| `AUTH_LDAP_*` | `SERVER`, `USE_TLS`, `SEARCH`, `UID_FIELD`, `BIND_USER`, `BIND_PASSWORD`, `APPEND_DOMAIN`, `USERNAME_FORMAT`, `GROUP_FIELD` (default `memberOf`), `SEARCH_FILTER`, `FIRSTNAME_FIELD`, `LASTNAME_FIELD`, `EMAIL_FIELD`, `ALLOW_SELF_SIGNED`, TLS cert keys |
| `AUTH_RATE_LIMITED`, `RATELIMIT_ENABLED`, `AUTH_RATE_LIMIT` | Rate-limit login brute force (e.g. `AUTH_RATE_LIMIT = "1 per 10 seconds"`, needs Flask-Limiter) |
| `OAUTH_PROVIDERS` | List of dicts: `name`, `icon`, `token_key`, `remote_app` (`client_id`, `client_secret`, `api_base_url`, `access_token_url`, `authorize_url`, ...) |
| `SAML_PROVIDERS`, `SAML_CONFIG` | SAML IdP list + SP settings (see docs/security) |
| `APP_NAME`, `APP_ICON`, `APP_THEME` | Name, icon, Bootswatch theme |
| `UPLOAD_FOLDER`, `FILE_ALLOWED_EXTENSIONS` | File uploads |
| `IMG_UPLOAD_FOLDER`, `IMG_UPLOAD_URL`, `IMG_SIZE`, `IMG_UPLOAD_RELATIVE_PATH` | Image uploads/resizing |
| `BABEL_DEFAULT_LOCALE`, `LANGUAGES` | i18n default + language dict `{'en': {'flag': 'gb', 'name': 'English'}}` |
| `LOGOUT_REDIRECT_URL` | Post-logout redirect |
| `FAB_UPDATE_PERMS` | Auto create/delete permissions on boot (default True; set False before `security-converge`) |
| `FAB_ADD_SECURITY_API` | [Beta] CRUD REST API for users/roles/permissions under `/api/v1/security/` |
| `FAB_ADD_SECURITY_VIEWS` | Disable all bundled security views |
| `FAB_API_MAX_PAGE_SIZE`, `FAB_API_SHOW_STACKTRACE`, `FAB_API_SWAGGER_UI`, `FAB_API_SWAGGER_TEMPLATE`, `FAB_OPENAPI_SERVERS` | REST/OpenAPI behavior |
| `FAB_API_ALLOW_JSON_QS` | Allow JSON query strings (default True) |
| `FAB_PASSWORD_COMPLEXITY_ENABLED`, `FAB_PASSWORD_COMPLEXITY_VALIDATOR` | Password policy for AUTH_DB |
| `FAB_PASSWORD_HASH_METHOD` (default `scrypt`), `FAB_PASSWORD_HASH_SALT_LENGTH` (16) | Hashing |
| `FAB_ROLES`, `FAB_ROLES_MAPPING` | Builtin read-only roles with regex permissions |
| `FAB_INDEX_VIEW`, `FAB_MENU`, `FAB_BASE_TEMPLATE`, `FAB_SECURITY_MANAGER_CLASS`, `FAB_STATIC_FOLDER`, `FAB_STATIC_URL_PATH` | Class path overrides |
| `FAB_SAFE_REDIRECT_HOSTS` | Allowed hosts for safe redirects |
| `ADDON_MANAGERS` | List of addon manager class paths |
| `SQLALCHEMY_BINDS`, `__bind_key__` | Multiple databases (vertical partitioning) |
| `RECAPTCHA_PUBLIC_KEY`, `RECAPTCHA_PRIVATE_KEY`, `MAIL_*` | Required for DB user self-registration (email activation via Flask-Mail) |

## Models (app/models.py)

Inherit from F.A.B.'s `Model` (same declarative space as the security models):

```python
from sqlalchemy import Column, Integer, String, Date, ForeignKey
from sqlalchemy.orm import relationship
from flask_appbuilder import Model

class ContactGroup(Model):
    __tablename__ = "contact_group"          # required since 5.0
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)

    def __repr__(self):
        return self.name

class Contact(Model):
    __tablename__ = "contact"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    address = Column(String(564), default="Street ")
    birthday = Column(Date)
    contact_group_id = Column(Integer, ForeignKey("contact_group.id"))
    contact_group = relationship("ContactGroup")

    def __repr__(self):
        return self.name
```

- `unique`, `nullable`, `default` are enforced by auto-generated forms/validators.
- **Audit columns** (created/changed by user + timestamps): inherit `AuditMixin`:
  `from flask_appbuilder.models.mixins import AuditMixin` — adds `created_on`,
  `changed_on`, `created_by`, `changed_by`. Exclude them from add/edit columns.
- Add a `__repr__` — it is what related/combo fields display.
- Methods and `@property` can be referenced like columns in `list_columns` etc.
- Format a method as a column with `@renders('col_name')`:
  `from flask_appbuilder.models.decorators import renders` — lets a method
  render a Model column's value (e.g. `<b>bold</b>` via `Markup`).
- Relations: standard SQLAlchemy `relationship()` with ForeignKey; many-to-many via
  `Table` + `secondary=` (select2 widget is generated automatically). Mark a M2M
  relationship required in REST APIs with `info={"required": True}`.
- Composite primary keys are supported (SQLAlchemy only, since 1.9.6).
- Enum columns for REST APIs: `Column(Enum(BookType), info={"marshmallow_enum": {"by_value": False}})`.

## Views

### View hierarchy

`BaseView` → `IndexView`, `SimpleFormView`/`PublicFormView`, `BaseModelView` →
`BaseChartView` (→ chart views) and `BaseCRUDView` → `ModelView`;
also `MasterDetailView`, `MultipleView`, `CompactCRUDMixin`.

### BaseView (custom pages)

Each view class is automatically registered as a Flask blueprint. Expose routes
with `@expose`, protect them with `@has_access`.

```python
from flask import render_template
from flask_appbuilder import AppBuilder, expose, BaseView, has_access
from app import appbuilder

class MyView(BaseView):
    route_base = "/myview"
    default_view = "method1"                 # menu href target

    @expose("/method1/")
    @has_access                              # creates permission "can method1 on MyView"
    def method1(self):
        return "Hello"

    @expose("/method3/<string:param1>")
    @has_access
    def method3(self, param1):
        self.update_redirect()               # record back-navigation history (5 entries)
        return self.render_template("method3.html", param1=param1)

appbuilder.add_view(MyView, "Method1", category="My View")
appbuilder.add_link("Method2", href="/myview/method2/john", category="My View")
```

- `appbuilder.add_view(ViewClass | instance, name, href='', icon='fa-...', label='',
  category='', category_icon='', category_label='', menu_cond=callable)` — registers
  view + menu entry.
- `appbuilder.add_view_no_menu(MyView())` — register without menu (no permissions).
- `appbuilder.add_link(name, href, ...)` / `appbuilder.add_separator(category)` —
  menu links/separators.
- Templates must extend `appbuilder/base.html` and override `{% block content %}`;
  always render via `self.render_template(...)` (base_template and appbuilder are
  injected automatically).
- `@expose(url='/...', methods=('GET',))` — allowed HTTP methods.
- Permission-name aggregation: `@permission_name('X')` (must be above `@has_access`).
- Hooks: `@before_request` and `@before_request(only=["create", "update", ...])`
  (from `flask_appbuilder.hooks`) run before each handler; returning non-None aborts.

### SimpleFormView / PublicFormView (non-model forms)

```python
from flask import flash
from flask_appbuilder import SimpleFormView
from flask_appbuilder.fieldwidgets import BS3TextFieldWidget
from flask_appbuilder.forms import DynamicForm
from wtforms import StringField
from wtforms.validators import DataRequired
from flask_babel import lazy_gettext as _

class MyForm(DynamicForm):
    field1 = StringField("Field1", description="Your field number one!",
                         validators=[DataRequired()], widget=BS3TextFieldWidget())

class MyFormView(SimpleFormView):
    form = MyForm
    form_title = "This is my first form view"
    message = "My form submitted"

    def form_get(self, form):          # pre-fill / pre-process (GET)
        form.field1.data = "This was prefilled"

    def form_post(self, form):         # post-process (POST); return None or a Response
        flash(self.message, "info")

appbuilder.add_view(MyFormView, "My form View", icon="fa-group",
                    label=_("My form View"), category="My Forms", category_icon="fa-cogs")
```

### ModelView (auto CRUD)

```python
from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

class GroupModelView(ModelView):
    datamodel = SQLAInterface(ContactGroup)
    related_views = [ContactModelView]       # master/detail tabs on show & edit

class ContactModelView(ModelView):
    datamodel = SQLAInterface(Contact)
    label_columns = {"contact_group": "Contacts Group"}
    list_columns = ["name", "personal_cellphone", "birthday", "contact_group"]
    show_fieldsets = [
        ("Summary", {"fields": ["name", "address", "contact_group"]}),
        ("Personal Info", {"fields": ["birthday", "personal_phone", "personal_cellphone"],
                           "expanded": False}),
    ]
```

Register:

```python
db.create_all()
appbuilder.add_view(GroupModelView, "List Groups", icon="fa-folder-open-o",
                    category="Contacts", category_icon="fa-envelope")
appbuilder.add_view(ContactModelView, "List Contacts", icon="fa-envelope",
                    category="Contacts")
```

Key configurable properties (all optional):

- **Columns**: `list_columns`, `show_columns`, `add_columns`, `edit_columns`,
  `search_columns`, plus `*_exclude_columns` variants. Dotted notation
  (`"contact_group.name"`) enables ordering on relationship fields. `label_columns`,
  `description_columns`, `formatters_columns = {'col': lambda x: ...}`.
- **Fieldsets (Django-style)**: `show_fieldsets`, `add_fieldsets`, `edit_fieldsets` —
  list of `(title, {'fields': [...], 'expanded': bool})`.
- **Forms**: `add_form`/`edit_form` (own WTF form), `add_form_extra_fields` /
  `edit_form_extra_fields` (extra non-model fields, e.g. a confirmation field),
  `add_form_query_rel_fields` / `edit_form_query_rel_fields` /
  `search_form_query_rel_fields` = `{'rel_col': [['rel_col', FilterClass, 'value']]}`,
  `validators_columns = {'field': [EqualTo('other', message=...)]}`,
  `read_only_columns`-style widgets (subclass `BS3TextFieldWidget` and set
  `kwargs['readonly'] = 'true'`).
- **Query behavior**: `base_filters = [['created_by', FilterEqualFunction, get_user],
  ['name', FilterStartsWith, 'a']]` (import filter classes from
  `flask_appbuilder.models.sqla.filters`), `base_order = ('col', 'asc')`,
  `search_exclude_columns`, `order_columns`, `page_size` (default 25).
- **Security**: `base_permissions = ['can_list', 'can_show']` to restrict generated
  permissions; `class_permission_name`, `method_permission_name`.
- **Templates/widgets**: `list_template`, `add_template`, `edit_template`,
  `show_template`, `list_widget`, `add_widget`, `edit_widget`, `show_widget`,
  `related_views` (ModelViews or chart views).
- **Lifecycle hooks**: `pre_add(item)`, `post_add(item)`, `pre_update(item)`,
  `post_update(item)`, `pre_delete(item)`, `post_delete(item)` (raise an exception
  in `pre_*` to abort and show the message), `prefill_form(form, pk)` (GET edit),
  `process_form(form, is_created)` (POST add/edit), redirect overrides
  `post_add_redirect`, `post_edit_redirect`, `post_delete_redirect`.
- `exclude_route_methods = {"delete", "edit"}` / `include_route_methods = {"list"}`
  to disable/enable builtin endpoints.
- Rate limit per view: `limits = [Limit("2 per 5 second")]` (flask-limiter).

### MasterDetailView, MultipleView, CompactCRUDMixin

```python
from flask_appbuilder.views import MasterDetailView, CompactCRUDMixin, ModelView

class GroupMasterView(MasterDetailView):
    datamodel = SQLAInterface(Group)
    related_views = [ContactModelView, ContactTimeChartView]   # charts allowed too

class MyInlineView(CompactCRUDMixin, ModelView):   # add/edit inline on the list page
    datamodel = SQLAInterface(MyInlineTable)

class BothViews(MultipleView):                     # several views on one page
    views = [GroupModelView, ContactModelView]
```

## Chart Views

```python
from flask_appbuilder.charts.views import DirectByChartView, GroupByChartView
from flask_appbuilder.models.group import aggregate_count, aggregate_sum, aggregate_avg

class CountryDirectChartView(DirectByChartView):
    datamodel = SQLAInterface(CountryStats)
    chart_title = "Direct Data Example"
    definitions = [
        {"label": "Unemployment", "group": "stat_date",
         "series": ["unemployed_perc", "college_perc"]},
    ]

class CountryGroupByChartView(GroupByChartView):
    datamodel = SQLAInterface(CountryStats)
    chart_title = "Statistics"
    definitions = [
        {"label": "Country Stat", "group": "country",   # or a Model method name
         "series": [(aggregate_avg, "unemployed_perc"), (aggregate_avg, "college_perc")]},
        {"group": "month_year", "formatter": pretty_month_year,   # formats the group value
         "series": [(aggregate_avg, "unemployed_perc")]},
    ]

appbuilder.add_view(CountryDirectChartView, "Show Country Chart",
                    icon="fa-dashboard", category="Statistics")
```

- `definitions` grammar: `{'label': str, 'group': '<COLNAME>|<MODEL FUNCNAME>',
  'formatter': func, 'series': ['<COL>'|'<MODEL FUNC>', ...] | [(agg_func, '<COL>'), ...]}`.
- `BaseChartView` options: `chart_title`, `chart_type` (`PieChart` default,
  `ColumnChart`, `LineChart`, `BarChart`, `AreaChart`), `chart_3d`, `width`,
  `height`, `group_by_label`; plus `BaseModelView` options (`base_filters`,
  `label_columns`, ...).
- Deprecated (but documented): `ChartView` (group-by pie/column) and `TimeChartView`
  (group by month/year) with `group_by_columns`, `direct_columns`.
- Charts can be added to `related_views`.

## Actions (record buttons)

```python
from flask import redirect
from flask_appbuilder.actions import action

class GroupModelView(ModelView):
    datamodel = SQLAInterface(Group)

    @action("myaction", "Do something on this record", "Do you really want to?", "fa-rocket")
    def myaction(self, item):
        return redirect(self.get_redirect())

    @action("muldelete", "Delete", "Delete all Really?", "fa-rocket", single=False)
    def muldelete(self, items):               # single=False: list view only
        self.datamodel.delete_all(items)
        self.update_redirect()
        return redirect(self.get_redirect())
```

`@action(name, text, confirmation=None, icon=None, multiple=True, single=True)`
— `multiple` = show on list view (receives a list), `single` = show on show view
(receives one item). Permissions are created per action.

## Files and Images

```python
from flask_appbuilder.models.mixins import ImageColumn
from flask_appbuilder.filemanager import ImageManager, get_file_original_name
from flask import Markup, url_for

class Person(Model):
    __tablename__ = "person"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    photo = Column(ImageColumn(size=(300, 300, True), thumbnail_size=(30, 30, True)))

    def photo_img(self):
        im = ImageManager()
        if self.photo:
            return Markup(f'<a href="{url_for("PersonModelView.show", pk=str(self.id))}" class="thumbnail">'
                          f'<img src="{im.get_url(self.photo)}" class="img-rounded img-responsive"></a>')
        return ""

class PersonModelView(ModelView):
    datamodel = SQLAInterface(Person)
    list_widget = ListThumbnail
    label_columns = {"photo_img": "Photo"}
    list_columns = ["photo_img", "name"]
    show_columns = ["photo_img", "name"]
```

- Requires `IMG_UPLOAD_FOLDER`, `IMG_UPLOAD_URL` (and optionally `IMG_SIZE`) in config.
- Files: `Column(FileColumn)` + `UPLOAD_FOLDER` + `FILE_ALLOWED_EXTENSIONS`;
  use `get_file_original_name(name)` to strip the `<UUID>_sep_` prefix.
- Image files are saved as `<uuid>_sep_<filename>` and `..._thumb`; `ImageManager`
  offers `get_url` / `get_url_thumbnail`.
- Library: `flask_appbuilder.filemanager` / `flask_appbuilder.models.mixins`.
- Pillow is required for image processing (`pip install Pillow`).

## Security

### Authentication types (import from `flask_appbuilder.security.manager`)

| Constant | Meaning | Extra deps |
|---|---|---|
| `AUTH_DB` | username + hashed password in DB | — |
| `AUTH_LDAP` | LDAP / Active Directory | `python-ldap` |
| `AUTH_REMOTE_USER` | web server env var `REMOTE_USER` (proxied auth) | — |
| `AUTH_OAUTH` | OAuth 1/2 providers | `authlib` |
| `AUTH_SAML` | SAML 2.0 (Entra ID, Okta, OneLogin...) | `pip install flask-appbuilder[saml]` |

OpenID 2.0 and MongoDB were **removed in 5.0** — do not use `AUTH_OID`.

```python
AUTH_TYPE = AUTH_DB
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Public"
```

- LDAP: see config table above. Role sync via `AUTH_ROLES_MAPPING`
  (`{"cn=fab_admins,ou=groups,dc=example,dc=org": ["Admin"]}`) + `AUTH_LDAP_GROUP_FIELD`;
  set `AUTH_ROLES_SYNC_AT_LOGIN = True` to re-sync on each login.
  LDAP over TLS: `ldaps://` scheme with `AUTH_LDAP_USE_TLS = False`; STARTTLS:
  `ldap://` + `AUTH_LDAP_USE_TLS = True`.
- OAuth providers: list in `OAUTH_PROVIDERS` with `name` (builtin userinfo getters
  for: `authentik`, `azure`, `github`, `google`, `keycloak`, `keycloak_before_17`,
  `linkedin`, `okta`, `openshift`, `twitter`), `icon`, `token_key`, `remote_app`.
  Customize userinfo with `@appbuilder.sm.oauth_user_info_getter` (returns dict with
  keys matching the User model, optionally including `role_keys`).
- SAML: `SAML_PROVIDERS` (name, icon, idp, attribute_mapping) + `SAML_CONFIG`
  (SP endpoints: `/saml/acs/`, `/saml/slo/`, `/saml/metadata/`). Auto-registered
  endpoints: `/login/`, `/login/<idp>`, `/saml/acs/`, `/saml/slo/`, `/saml/metadata/`.
- User self-registration (AUTH_DB): needs `RECAPTCHA_*` and `MAIL_*` config;
  users are activated via emailed link. Override by subclassing `RegisterUserDBView`
  and setting `registeruserdbview` on your SecurityManager.
- Rate limiting: `AUTH_RATE_LIMITED = True` + `RATELIMIT_ENABLED = True`.

### Roles and permissions

- Permissions are generated by inspecting exposed methods: each becomes
  `<permission> on <ViewClassName>` (e.g. `can add on ContactModelView`).
- `ModelView` generates: `can_list`, `can_show`, `can_add`, `can_edit`,
  `can_delete`, `can_download`.
- `ModelRestApi` generates: `can_get`, `can_put`, `can_post`, `can_delete`,
  `can_info`.
- `@has_access` methods generate `can <methodname> on <ViewName>`.
- Special roles: **Admin** (`AUTH_ROLE_ADMIN`, full access) and **Public**
  (`AUTH_ROLE_PUBLIC`, for unauthenticated users; add permissions here to make
  views public).
- Builtin read-only roles (regex, no DB M2M): `FAB_ROLES = {"ReadOnly":
  [[".*", "can_list"], [".*", "can_show"], [".*", "menu_access"],
  [".*", "can_get"], [".*", "can_info"]]}` (renaming via `FAB_ROLES_MAPPING`).
- Compress permissions: `class_permission_name = "api"` on several views/APIs and
  `method_permission_name = {"get_list": "read", ...}`; migrate existing installs
  with `FAB_UPDATE_PERMS = False` + `flask fab security-converge` (use
  `previous_class_permission_name` / `previous_method_permission_name`; dry-run
  with `--dry-run`; **backup your DB first**).
- Cleanup orphaned permissions after renames:
  `appbuilder.security_cleanup()` (call after all views are registered) or
  `flask fab security-cleanup`.

### Custom SecurityManager / extended User

```python
# security.py
from flask_appbuilder.security.views import UserDBModelView
from flask_appbuilder.security.sqla.manager import SecurityManager

class MyUserDBView(UserDBModelView):
    list_columns = ["first_name", "last_name", "username", "email", "active", "roles"]

class MySecurityManager(SecurityManager):
    userdbmodelview = MyUserDBView          # userldapmodelview, useroauthmodelview,
                                            # userremoteusermodelview, usersamlmodelview...

# __init__.py
appbuilder = AppBuilder(app, db.session, security_manager_class=MySecurityManager)
# or config:  FAB_SECURITY_MANAGER_CLASS = "app.security.MySecurityManager"
```

Extend the User model: subclass `from flask_appbuilder.security.sqla.models import
User` (`__tablename__ = "ab_user"` + extra columns), override `user_model` and the
matching user view on your SecurityManager.

- Optional Flask-Talisman support: initialize `Talisman(app)`; F.A.B. provides
  CSP nonces via `csp_nonce()` in Jinja2.

## REST API (JWT)

### BaseApi — custom endpoints

```python
from flask_appbuilder.api import BaseApi, expose, rison, safe
from flask_appbuilder.security.decorators import protect

class ExampleApi(BaseApi):
    route_base = "/newapi/v2/nice"        # override; default /api/v1/<lowercase class name>
    # resource_name = "example"           # or override just the resource
    # version = "v2"                      # or the version

    @expose("/greeting")
    def greeting(self):
        """Send a greeting
        ---
        get:
          responses:
            200:
              description: Greet the user
        """
        return self.response(200, message="Hello")

    @expose("/greeting3")
    @rison()                              # parse ?q=(name:daniel) into kwargs["rison"]
    def greeting3(self, **kwargs):
        if "name" in kwargs["rison"]:
            return self.response(200, message=f"Hello {kwargs['rison']['name']}")
        return self.response_400(message="Please send your name")

    @expose("/private")
    @protect()                            # JWT (or allow_browser_login=True for cookies)
    def private(self):
        return self.response(200, message="This is private")

    @expose("/error")
    @safe                                 # JSON error responses for uncaught exceptions
    def error(self):
        raise Exception

appbuilder.add_api(ExampleApi)
```

- `route_base` default: `/api/v1/<lowercase_class_name>`; `resource_name` and
  `version` properties override the inferred segments.
- `self.response(code, **kwargs)` + helpers `response_400/401/403/404/422/500`.
- `@rison(schema_dict)` validates with a JSON schema; invalid Rison returns 400.
  Rison ↔ Python: `import prison; prison.dumps({...}) / prison.loads(...)`.
- OpenAPI: YAML docstrings in each method; specs served at `/api/v1/_openapi`;
  packaged response refs: `#/components/responses/{400,401,404,422,500}`; register
  reusable parameter schemas with `apispec_parameter_schemas`; per-method spec
  merging via `openapi_spec_methods`; Swagger UI with `FAB_API_SWAGGER_UI = True`
  at `/swagger/v1`.
- Security: `@protect(allow_browser_login=True)` accepts flask-login cookies;
  `base_permissions = ["can_private"]` restricts generated permissions.
- `@before_request(only=[...])` hooks also work on APIs.

### JWT authentication

```bash
curl -XPOST http://localhost:8080/api/v1/security/login -d \
  '{"username": "admin", "password": "password", "provider": "db"}' \
  -H "Content-Type: application/json"
# {"access_token": "<TOKEN>"}   (+ refresh token if "refresh": true)
export TOKEN="<TOKEN>"
curl http://localhost:8080/api/v1/example/private -H "Authorization: Bearer $TOKEN"
```

- `provider` is `db` or `ldap`; refresh endpoint for non-fresh tokens; custom JWT
  loader: `@appbuilder.sm.jwt_manager.user_loader_callback_loader`.
- Built on flask-jwt-extended (see its docs for extra JWT options).

### ModelRestApi — auto CRUD API

```python
from flask_appbuilder.api import ModelRestApi
from flask_appbuilder.models.sqla.interface import SQLAInterface

class GroupModelApi(ModelRestApi):
    resource_name = "group"
    datamodel = SQLAInterface(ContactGroup)
    # list_columns / show_columns / add_columns / edit_columns / search_columns ...
    # base_filters = [["name", FilterStartsWith, "A"]]
    # base_order = ("name", "desc")
    # page_size = 20
    # exclude_route_methods = {"put", "post", "delete", "info"}

appbuilder.add_api(GroupModelApi)
```

Endpoints (all protected, all require JWT):

| URL | Method | Description | Permission |
|---|---|---|---|
| `/_info` | GET | CRUD meta data (add/edit fields, filters, permissions) | `can_info` |
| `/` | GET | list, Rison args `(filters:!((col:name,opr:sw,value:a)),columns:!(name),page:2,page_size:2,order_column:name,order_direction:desc)` | `can_get` |
| `/<PK>` | GET | single item, Rison `(columns:!(name,address),keys:!(label_columns))`; `keys:!(none)` drops meta | `can_get` |
| `/` | POST | create (201) | `can_post` |
| `/` | PUT | update (works as PATCH when unrestricted) | `can_put` |
| `/<PK>` | DELETE | delete | `can_delete` |

- **Meta-data keys** on GETs: `(keys:!(permissions,add_columns,label_columns,...))`;
  i18n with `?_l_=pt`.
- **filters**: list of `{"col": ..., "opr": <operator from _info>, "value": ...}` —
  all ANDed. `opr` examples: `sw` (startswith), `ew` (endswith). Dotted columns
  (`contact_group.name`) auto-join.
- **Server-side defaults**: `base_filters`, `base_order`, `page_size`,
  `list_columns`, `show_columns`, `add_columns`, `edit_columns`, `search_columns`,
  `show_select_columns`/`list_select_columns` (select extra cols for @property-based
  output), `order_rel_fields = {'contact_group': ('name', 'asc')}`,
  `add_query_rel_fields`/`edit_query_rel_fields`.
- **Validation**: marshmallow-sqlalchemy infers schemas; errors return 422 with
  `{"message": {col: [errors]}}`. Customize:
  - `validators_columns = {"name": validate_name}` (simplest),
  - or full `add_model_schema`/`edit_model_schema`/`list_model_schema`/
    `show_model_schema` (subclass `BaseModelSchema` with `model_cls = ContactGroup`).
- **Hooks**: `pre_add/post_add(pre/post_update/pre_update/pre_delete/post_delete(item)`,
  `pre_get(data)`, `pre_get_list(data)`.
- `include_route_methods`/`exclude_route_methods`; `max_page_size`
  (`FAB_API_MAX_PAGE_SIZE`, `-1` = unlimited); `list_outer_default_load`/
  `show_outer_default_load`; `openapi_spec_tag`, `openapi_spec_component_schemas`
  (marshmallow schemas with `__component_name__`).
- The legacy AJAX `/api` endpoints of ModelView still exist but are deprecated
  (to be removed) — use ModelRestApi.

## Command Line (`flask fab ...`)

Set `FLASK_APP` first: `export FLASK_APP=app` or
`FLASK_APP="app:create_app('config')"` (factory pattern).

| Command | Purpose |
|---|---|
| `create-app [--name X]` | Download skeleton application (requires internet) |
| `create-addon --name X` | Download skeleton addon (`fab_addon_X`) |
| `create-admin` | Create admin user (asks for password; assumes AUTH_DB) |
| `create-user` | Create user with arbitrary role |
| `create-db` | Create all DB objects (SQLAlchemy) |
| `upgrade-db` | Upgrade DB after F.A.B. upgrade |
| `reset-password` | Reset a user's password |
| `list-users` / `list-views` | List users / registered views |
| `export-roles` / `import-roles` | Roles+permissions ↔ JSON file |
| `security-cleanup` | Remove orphan permissions |
| `security-converge [--dry-run]` | Migrate permission names (`previous_*` attrs) |
| `babel-extract [-k key ...]` | Extract translation strings |
| `babel-compile` | Compile translations |
| `collect-static` | Copy F.A.B. static files into app |
| `version` | Show F.A.B. version |

## i18n (Babel)

```bash
flask fab babel-extract          # extracts to ./babel/messages.pot
pybabel init -i ./babel/messages.pot -d app/translations -l pt
# translate app/translations/pt/LC_MESSAGES/messages.po
flask fab babel-compile
```

- Mark strings with `from flask_babel import lazy_gettext as _` in views/config and
  `{{ _("text") }}` in templates.
- Register languages: `LANGUAGES = {'en': {'flag': 'gb', 'name': 'English'},
  'pt': {'flag': 'pt', 'name': 'Portuguese'}}`.
- 16 builtin translations shipped.

## Multiple Databases

```python
SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "app.db")
SQLALCHEMY_BINDS = {
    "my_sql1": "mysql://root:password@localhost/quickhowto",
    "my_sql2": "mysql://root:password@externalserver.domain.com/quickhowto2",
}

class Model1(Model):
    __tablename__ = "model1"
    __bind_key__ = "my_sql1"
    id = Column(Integer, primary_key=True)
```

Security tables always live on the default bind.

## Templates and Widgets

- Base template blocks (`appbuilder/baselayout.html`): `head_meta`, `head_css`,
  `head_js`, `body` → `navbar`, `messages`, `content`, `footer`, `tail_js`.
  Add app-wide CSS/JS by extending and overriding `head_css`/`head_js`/`tail_js`
  with `{{ super() }}`.
- Custom base layout: `AppBuilder(app, db.session, base_template='mybase.html')`.
- Navbar partials: `appbuilder/navbar_menu.html`, `appbuilder/navbar_right.html`.
- ModelView templates use block structure — override with `{{ super() }}`:
  - list: blocks `list_search`, `list_list`
  - add: block `add_form`; edit: block `edit_form`; show: block `show_form`
  - cascade show/edit (related views): extra block `related_views`;
    templates `appbuilder/general/model/show_cascade.html` / `edit_cascade.html`
  - set via `list_template`/`add_template`/`edit_template`/`show_template`.
- Widgets available: `ListWidget` (default), `ListLinkWidget`, `ListThumbnail`,
  `ListItem`, `ListBlock`, `FormWidget`, `FormHorizontalWidget`,
  `FormInlineWidget`, `ShowWidget`, `ShowBlockWidget`, `ShowVerticalWidget`.
  Custom widget: subclass (e.g. `class MyWidgetList(ListWidget): template =
  'widgets/my_widget_list.html'`) and assign to `list_widget` on the view.
  List widgets extend `appbuilder/general/widgets/base_list.html` (blocks
  `list_header`, `begin_content`, `begin_loop_header`, `begin_loop_values`);
  template vars: `can_show/can_edit/can_add/can_delete`, `value_columns`,
  `include_columns`, `order_columns`, `pks`, `actions`, `modelview_name`.
- Library: `{% import 'appbuilder/general/lib.html' as lib %}` —
  `lib.panel_begin()/panel_end()`, `lib.accordion_tag(...)`, `lib.btn_crud(...)`,
  `lib.render_action_links(actions, pk, modelview_name)`, `lib.lnk_back()`.
- Theme: `APP_THEME = "spacelab.css"` (Bootswatch). Custom index:
  subclass `IndexView` (`index_template = 'my_index.html'`) and pass
  `indexview=MyIndexView` to AppBuilder (or `FAB_INDEX_VIEW`).
- Menu icons are Font-Awesome names (e.g. `fa-envelope`, `fa-dashboard`,
  `fa-rocket`) — see https://fontawesome.io/icons/.

## AddOns

```bash
flask fab create-addon --name first     # creates fab_addon_first
```

Manager-based modular apps:

```python
from flask_appbuilder.basemanager import BaseManager

class FirstAddOnManager(BaseManager):
    def register_views(self):
        self.appbuilder.add_view(FirstModelView1, "First View1",
                                 icon="fa-user", category="First AddOn")
    def pre_process(self):   # before register_views — seed data etc.
        pass
    def post_process(self):  # after register_views
        pass
```

Consume the addon from any app:

```python
ADDON_MANAGERS = ["fab_addon_first.manager.FirstAddOnManager"]
```

## Generic Data Sources (beta)

Wrap non-SQLAlchemy data (commands, LDAP, Python libs) as models:

```python
from flask_appbuilder.models.generic import GenericModel, GenericSession, GenericColumn
from flask_appbuilder.models.sqla.interface import GenericInterface

class PSModel(GenericModel):
    PID = GenericColumn(int, primary_key=True)
    UID = GenericColumn(str)
    CMD = GenericColumn(str)

class PSSession(GenericSession):
    def all(self):
        self.delete_all(PSModel())
        for line in os.popen("ps -ef"):
            self._add_object(line)       # parse + self.add(PSModel(...))
        return super().all()
    def get(self, pk):
        ...

class PSView(ModelView):
    datamodel = GenericInterface(PSModel, PSSession())
    base_permissions = ["can_list", "can_show"]
```

`GenericSession` implements filtering/ordering on top of your `all()`/`get()`.

## Project Layout / Integration Notes

- **App factory pattern** is supported: create everything in a `create_app()`
  function and use `AppBuilder.init_app(app, session)` or init in the factory;
  use `FAB_SECURITY_MANAGER_CLASS = "app.security.MySecurityManager"` for custom
  security managers with factories.
- **Application context**: in v5+, queries through the security manager require
  `with app.app_context(): ...`; use `from flask import current_app` instead of
  the removed `appbuilder.get_app`; use `appbuilder.sm.session` (not
  `get_session`).
- **FAB_CREATE_DB** (default True) controls automatic table creation.
- `appbuilder.session` property returns the current SQLAlchemy session;
  `appbuilder.version` the F.A.B. version; `appbuilder.app_name/app_icon/app_theme`.
- Models require explicit `__tablename__` on F.A.B. 5.x.
- F.A.B. 5.x: SQLAlchemy 2.x / Flask-SQLAlchemy 3.x compatible; `SQLAInterface`
  no longer swallows exceptions and has `commit=False` support.
- Menu conditions: `menu_cond=lambda: feature_enabled()` on `add_view`/`add_link`;
  `add_separator(category, cond=...)`.
- `flask fab create-app` no longer accepts `--engine` (5.x).

## Common Recipes

**Restrict a view to read-only:**
```python
class MyView(ModelView):
    datamodel = SQLAInterface(MyModel)
    base_permissions = ["can_list", "can_show"]
```

**Filter by current user (row-level security):**
```python
from flask import g
from flask_appbuilder.models.sqla.filters import FilterEqualFunction

def get_user():
    return g.user

class MyView(ModelView):
    datamodel = SQLAInterface(MyTable)
    base_filters = [["created_by", FilterEqualFunction, get_user]]
```

**Custom validation on REST API:**
```python
from marshmallow import ValidationError

def validate_name(n):
    if n[0] != "A":
        raise ValidationError("Name must start with an A")

class GroupModelRestApi(ModelRestApi):
    resource_name = "group"
    datamodel = SQLAInterface(ContactGroup)
    validators_columns = {"name": validate_name}
```

**Make a custom page:**
```python
class MyPage(BaseView):
    route_base = "/mypage"
    default_view = "index"

    @expose("/")
    @has_access
    def index(self):
        return self.render_template("mypage.html", extra=self.read_data())
```

## References (source documentation)

Primary documentation — https://flask-appbuilder.readthedocs.io/en/latest/ :

- Index / overview: https://flask-appbuilder.readthedocs.io/en/latest/index.html
- Introduction (features): https://flask-appbuilder.readthedocs.io/en/latest/intro.html
- Installation: https://flask-appbuilder.readthedocs.io/en/latest/installation.html
- Command Line Manager (`flask fab`): https://flask-appbuilder.readthedocs.io/en/latest/cli.html
- Base Configuration (all config keys): https://flask-appbuilder.readthedocs.io/en/latest/config.html
- Base Views (BaseView, form views): https://flask-appbuilder.readthedocs.io/en/latest/views.html
- Model Views quick how-to: https://flask-appbuilder.readthedocs.io/en/latest/quickhowto.html
- Quick minimal application: https://flask-appbuilder.readthedocs.io/en/latest/quickminimal.html
- REST API (BaseApi/ModelRestApi/JWT/Rison/OpenAPI): https://flask-appbuilder.readthedocs.io/en/latest/rest_api.html
- Chart views: https://flask-appbuilder.readthedocs.io/en/latest/quickcharts.html
- Files and images: https://flask-appbuilder.readthedocs.io/en/latest/quickfiles.html
- Model relations / composite keys: https://flask-appbuilder.readthedocs.io/en/latest/relations.html
- Actions: https://flask-appbuilder.readthedocs.io/en/latest/actions.html
- Advanced configuration (filters, forms, validators): https://flask-appbuilder.readthedocs.io/en/latest/advanced.html
- Customizing (themes, index, widgets, view behaviors): https://flask-appbuilder.readthedocs.io/en/latest/customizing.html
- Templates: https://flask-appbuilder.readthedocs.io/en/latest/templates.html
- AddOn development: https://flask-appbuilder.readthedocs.io/en/latest/addons.html
- Generic data sources: https://flask-appbuilder.readthedocs.io/en/latest/generic_datasource.html
- Multiple databases: https://flask-appbuilder.readthedocs.io/en/latest/multipledbs.html
- i18n translations: https://flask-appbuilder.readthedocs.io/en/latest/i18n.html
- Security (auth types, roles, permissions, SAML): https://flask-appbuilder.readthedocs.io/en/latest/security.html
- User registration: https://flask-appbuilder.readthedocs.io/en/latest/user_registration.html
- Class diagrams: https://flask-appbuilder.readthedocs.io/en/latest/diagrams.html
- Full API reference: https://flask-appbuilder.readthedocs.io/en/latest/api.html
- Version migration (5.0 and older): https://flask-appbuilder.readthedocs.io/en/latest/versionmigration.html
- Breaking changes per version: https://flask-appbuilder.readthedocs.io/en/latest/breaking.html

Repositories and extras:

- Project: https://github.com/dpgaspar/Flask-AppBuilder
- Examples (quickhowto, crud_rest_api, base_api, oauth, saml, widgets,
  employees, composite_keys, extendsecurity, quickfiles, quickimages, ...):
  https://github.com/dpgaspar/Flask-AppBuilder/tree/master/examples
- Skeleton app: https://github.com/dpgaspar/Flask-AppBuilder-Skeleton
- Addon example: https://github.com/dpgaspar/fab_addon_audit
- Flask-JWT-Extended options (JWT config): https://flask-jwt-extended.readthedocs.io/en/latest/options.html
- Flask-Limiter (rate limiting): https://flask-limiter.readthedocs.io/en/stable/
- Flask-Babel (i18n): https://python-babel.github.io/flask-babel
- Rison/prison (URI arguments): https://github.com/betodealmeida/prison
- Issues/bugs/features: https://github.com/dpgaspar/Flask-AppBuilder/issues/new