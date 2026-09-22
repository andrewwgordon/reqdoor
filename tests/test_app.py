def test_app_factory(app):
    """Smoke-test that the app factory creates a valid Flask app."""
    assert app is not None
    assert app.config["TESTING"] is True


def test_index_responds(client):
    """The root / endpoint returns a valid response (FAB index is public by default)."""
    resp = client.get("/")
    assert resp.status_code in (200, 302)


def test_project_list_requires_login(client):
    """The Projects CRUD list endpoint requires authentication."""
    resp = client.get("/projectview/list/", follow_redirects=False)
    assert resp.status_code == 302
