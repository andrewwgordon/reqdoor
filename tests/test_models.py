from flask import g
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Project


def _login_context(app):
    """Bind a fake admin user to g.user so AuditMixin can populate audit columns."""
    user = app.appbuilder.sm.find_user(username="admin")
    g.user = user
    return user


def test_create_project(app):
    """Project can be created and persisted."""
    with app.app_context():
        _login_context(app)
        proj = Project(
            project_code="PRJ-001",
            name="Test Project",
            description="A test project for pytest",
        )
        db.session.add(proj)
        db.session.commit()

        fetched = (
            db.session.query(Project).filter_by(project_code="PRJ-001").first()
        )
        assert fetched is not None
        assert fetched.name == "Test Project"
        assert fetched.description == "A test project for pytest"


def test_project_repr(app):
    """__repr__ produces the expected bracketed format."""
    with app.app_context():
        proj = Project(project_code="PRJ-002", name="Another Project")
        assert repr(proj) == "[PRJ-002] Another Project"


def test_project_code_unique(app):
    """Project code must be unique."""
    with app.app_context():
        _login_context(app)
        p1 = Project(project_code="PRJ-003", name="First")
        db.session.add(p1)
        db.session.commit()

        p2 = Project(project_code="PRJ-003", name="Duplicate")
        db.session.add(p2)
        try:
            db.session.commit()
            assert False, "Expected IntegrityError for duplicate project_code"
        except IntegrityError:
            db.session.rollback()
