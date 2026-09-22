"""Tests for the security-table housekeeping in `app/security.py`.

Boot used to repeat this on the console, once per renamed or removed button::

    Refused to delete permission view, assoc with role exists ReviewView.activate Admin

Cause: F.A.B.'s `add_permissions_view()` deletes a stale permission-view row by
handing the *Permission* to `del_permission_role()` (which expects a
PermissionView), so the role link survives, the delete is refused, and it is
retried on every boot. `prune_stale_permissions()` removes the links first, so
there is nothing left to refuse.
"""

import logging

import pytest

from app.extensions import db
from app.security import live_pairs, prune_stale_permissions
from app.views import register_views

REFUSAL = "Refused to delete permission view"


def _pvm_pairs(sm):
    pairs = set()
    for view_menu in sm.get_all_view_menu():
        for perm_view in sm.find_permissions_view_menu(view_menu):
            pairs.add((perm_view.permission.name, view_menu.name))
    return pairs


def _permission_names(sm):
    return {p.name for p in sm.session.query(sm.permission_model).all()}


def _add_stale(sm, permission, view):
    """A permission-view row that nothing implements, granted to Admin (as F.A.B.
    would have done when the button still existed)."""
    perm_view = sm.add_permission_view_menu(permission, view)
    admin = sm.find_role(sm.auth_role_admin)
    if perm_view not in admin.permissions:
        sm.add_permission_role(admin, perm_view)
    return perm_view


@pytest.fixture
def stale_rows(app):
    """Seed the exact leftovers a rename produces, and hand back their names."""
    with app.app_context():
        sm = app.appbuilder.sm
        _add_stale(sm, "activate", "ReviewView")
        _add_stale(sm, "can_action_post", "ProjectView")
        _add_stale(sm, "can_add", "ApprovalView")
        yield ["activate", "can_action_post", "can_add"]


def test_prune_removes_stale_rows_and_their_role_links(app, stale_rows):
    sm = app.appbuilder.sm
    with app.app_context():
        admin = sm.find_role(sm.auth_role_admin)
        assert ("activate", "ReviewView") in _pvm_pairs(sm)
        assert any(p.view_menu.name == "ReviewView" for p in admin.permissions)

        removed = prune_stale_permissions(app.appbuilder)

        assert ("activate", "ReviewView") not in _pvm_pairs(sm)
        assert ("can_action_post", "ProjectView") not in _pvm_pairs(sm)
        assert ("can_add", "ApprovalView") not in _pvm_pairs(sm)
        assert removed["permission_views"] >= 3
        assert removed["role_links"] >= 1
        # the orphaned permission rows go too, once no view references them
        assert "activate" not in _permission_names(sm)


def test_prune_keeps_everything_that_is_live(app, stale_rows):
    sm = app.appbuilder.sm
    with app.app_context():
        before = _pvm_pairs(sm)
        prune_stale_permissions(app.appbuilder)
        after = _pvm_pairs(sm)

        for expected in (
            ("can_list", "ReviewView"),
            ("advance", "ReviewView"),          # C16's replacement button
            ("sign_off", "ReviewView"),         # C17's replacement button
            ("advance", "ChangeRequestView"),
            ("can_add", "ArtefactView"),
            ("menu_access", "Governance"),      # menu category
            ("menu_access", "Draft artefacts"), # saved-filter link
            ("menu_access", "Projects"),        # menu entry (named, not the view)
        ):
            assert expected in before, expected
            assert expected in after, expected

        # Admin still rules the app, and only the stale grants were unlinked.
        admin = sm.find_role(sm.auth_role_admin)
        admin_pairs = {(p.permission.name, p.view_menu.name) for p in admin.permissions}
        assert ("can_list", "ReviewView") in admin_pairs
        assert ("activate", "ReviewView") not in admin_pairs
        assert ("can_action_post", "ProjectView") not in admin_pairs


def test_prune_is_idempotent_and_never_refuses(app, stale_rows, caplog):
    sm = app.appbuilder.sm
    with app.app_context(), caplog.at_level(logging.WARNING, logger="flask_appbuilder"):
        first = prune_stale_permissions(app.appbuilder)
        assert REFUSAL not in caplog.text

        after_first = _pvm_pairs(sm)
        second = prune_stale_permissions(app.appbuilder)

        assert REFUSAL not in caplog.text
        assert _pvm_pairs(sm) == after_first
        assert second["permission_views"] == 0
        assert second["permissions"] == 0
        assert second["views"] == 0
        assert first["permission_views"] > 0


def test_registered_state_leaves_no_orphan_permissions(app):
    """The invariant that keeps boots quiet: nothing in the DB unimplemented.

    F.A.B.'s per-view sync complains about exactly these rows on every start-up.
    """
    sm = app.appbuilder.sm
    with app.app_context():
        register_views(app.appbuilder)
        live = live_pairs(app.appbuilder)
        menu_names = {
            item.name for cat in app.appbuilder.menu.get_list() for item in cat.childs
        } | {cat.name for cat in app.appbuilder.menu.get_list()}
        orphans = [
            pair
            for pair in _pvm_pairs(sm)
            if pair not in live and not (pair[0] == "menu_access" and pair[1] in menu_names)
        ]
        assert orphans == []


def test_registration_prunes_rows_left_by_an_earlier_release(app, caplog):
    """Self-healing: the first boot after a rename repairs itself, quietly."""
    sm = app.appbuilder.sm
    with caplog.at_level(logging.WARNING, logger="flask_appbuilder"):
        with app.app_context():
            _add_stale(sm, "verify", "ChangeRequestView")
            register_views(app.appbuilder)
            assert ("verify", "ChangeRequestView") not in _pvm_pairs(sm)
        assert not [r for r in caplog.records if REFUSAL in r.getMessage()]
