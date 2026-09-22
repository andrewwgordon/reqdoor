import pytest
from app.extensions import db
from app.models import Project


def _login_client(client, username="admin", password="admin"):
    """Authenticate via the standard FAB DB login form."""
    resp = client.post(
        "/login/",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    return resp


def test_project_list_authenticated(client):
    """Logged-in admin can access the Projects list."""
    _login_client(client)
    resp = client.get("/projectview/list/")
    assert resp.status_code == 200


def test_project_add_round_trip(client):
    """Admin can add a Project through the CRUD UI."""
    _login_client(client)

    resp = client.post(
        "/projectview/add",
        data={
            "project_code": "PRJ-UI-01",
            "name": "UI Project",
            "description": "Added via test client",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Verify it reached the list page after success
    # (FAB redirects to list on successful add)

    with client.application.app_context():
        proj = (
            db.session.query(Project).filter_by(project_code="PRJ-UI-01").first()
        )
        assert proj is not None
        assert proj.name == "UI Project"
