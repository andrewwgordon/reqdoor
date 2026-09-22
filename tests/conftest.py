import pytest
from flask import g

from app import create_app, appbuilder
from app.extensions import db
from app.models import (
    Artefact,
    ArtefactKind,
    Baseline,
    BaselineEntry,
    Priority,
    Project,
    RelationshipType,
    RequirementState,
)


def login_as(app):
    """Bind the admin user to g.user so AuditMixin can stamp audit columns."""
    g.user = app.appbuilder.sm.find_user(username="admin")
    return g.user


def add_artefact(
    project_id,
    title="Requirement 1",
    content="hello",
    kind=ArtefactKind.SYSTEM_REQ,
    status=RequirementState.DRAFT,
):
    a = Artefact(
        project_id=project_id,
        kind=kind,
        title=title,
        content=content,
        status=status,
        priority=Priority.MEDIUM,
    )
    db.session.add(a)
    db.session.commit()
    return a


def add_relationship_type(name="SATISFIES", display_name="Satisfies"):
    rt = RelationshipType(name=name, display_name=display_name, is_directional=True)
    db.session.add(rt)
    db.session.commit()
    return rt


def add_project(project_code="POC", name="PoC Project"):
    p = Project(project_code=project_code, name=name, description="fixture")
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def project(app):
    """A committed PoC project id, ready for use by service/UI helpers."""
    with app.app_context():
        login_as(app)
        return add_project().id


@pytest.fixture(scope="session")
def app():
    """Create application for testing with an in-memory database."""
    _app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    with _app.app_context():
        db.create_all()
        if not appbuilder.sm.find_user(username="admin"):
            appbuilder.sm.add_user(
                username="admin",
                first_name="Admin",
                last_name="User",
                email="admin@example.com",
                role=appbuilder.sm.find_role(appbuilder.sm.auth_role_admin),
                password="admin",
            )
        yield _app


@pytest.fixture(scope="function", autouse=True)
def clean_db(app):
    """Rollback and clear domain data after each test (FK cascade order)."""
    with app.app_context():
        yield
        db.session.rollback()
        from app.models import (
            Approval,
            Artefact,
            ArtefactRelationship,
            ArtefactVersion,
            Baseline,
            BaselineEntry,
            ChangeRequest,
            ChangeSetItem,
            Comment,
            Project,
            RelationshipType,
            Review,
            ReviewAssignment,
            ReviewVerdict,
        )

        # SQLite does not enforce FK cascades by default, so clear in child-first order
        db.session.query(BaselineEntry).delete()
        db.session.query(Approval).delete()
        db.session.query(Comment).delete()
        db.session.query(ReviewVerdict).delete()
        db.session.query(ReviewAssignment).delete()
        db.session.query(ChangeSetItem).delete()
        db.session.query(ArtefactRelationship).delete()
        db.session.query(Review).delete()
        db.session.query(ChangeRequest).delete()
        db.session.query(Baseline).delete()
        db.session.query(RelationshipType).delete()
        db.session.query(ArtefactVersion).delete()
        db.session.query(Artefact).delete()
        db.session.query(Project).delete()
        db.session.commit()


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()
