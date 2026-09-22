"""Permission housekeeping for the security tables.

Renaming or removing a view, a menu entry or a row action leaves rows behind in
``ab_permission`` / ``ab_view_menu`` / ``ab_permission_view``. F.A.B.'s own
``appbuilder.security_cleanup()`` only notices *whole views* it no longer knows;
afterwards it runs ``security_converge()``, which asks for the leftover
**permission** rows (``activate``, ``can_action_post``, ``can_add``...) to be
deleted while they still carry role links. F.A.B. refuses those, so every boot
repeats the attempt and fills the console with::

    Refused to delete permission view, assoc with role exists ReviewView.activate Admin

``prune_stale_permissions()`` does the same job at the right granularity: role
links first, then the permission-view rows, then the orphaned permissions and
view menus. It is quiet, idempotent, and only ever removes rows that no
registered view or menu item implements.
"""

import logging

log = logging.getLogger(__name__)

MENU_ACCESS = "menu_access"


def _menu_names(appbuilder):
    """Every name in the menu tree (categories, entries, saved-filter links)."""
    names = set()
    menu = getattr(appbuilder, "menu", None)
    if menu is None:
        return names

    def walk(items):
        for item in items:
            if item.name != "-":
                names.add(item.name)
            walk(item.childs or [])

    walk(menu.get_list())
    return names


def live_pairs(appbuilder):
    """The ``(permission, view_menu)`` pairs the running app really implements."""
    pairs = set()
    for view in appbuilder.baseviews:
        view_name = view.class_permission_name
        for permission in view.base_permissions or []:
            pairs.add((permission, view_name))
        # menu_access rows are managed by the menu, not by the view's routes
        pairs.add((MENU_ACCESS, view_name))
    return pairs


def prune_stale_permissions(appbuilder):
    """Delete security rows nothing implements any more.

    Returns a summary dict (mostly for logging and tests)::

        {"permission_views": 4, "permissions": 4, "views": 1, "role_links": 9}
    """
    sm = appbuilder.sm
    live = live_pairs(appbuilder)
    known_views = {view.class_permission_name for view in appbuilder.baseviews}
    menu_names = _menu_names(appbuilder)

    removed = {"permission_views": 0, "permissions": 0, "views": 0, "role_links": 0}

    for view_menu in list(sm.get_all_view_menu()):
        name = view_menu.name
        keep_rows = live if name in known_views else None
        for perm_view in list(sm.find_permissions_view_menu(view_menu)):
            permission_name = perm_view.permission.name
            if keep_rows is not None and (permission_name, name) in keep_rows:
                continue
            if keep_rows is None and name in menu_names and permission_name == MENU_ACCESS:
                # a menu entry (category or saved filter): F.A.B. owns this row
                continue
            for role in list(sm.get_all_roles()):
                if perm_view in role.permissions:
                    sm.del_permission_role(role, perm_view)
                    removed["role_links"] += 1
            sm.del_permission_view_menu(permission_name, name, cascade=False)
            removed["permission_views"] += 1
            log.info(
                "pruned stale permission %s on %s", permission_name, name
            )

        # A view menu nothing references any more (a renamed or removed view).
        if name not in known_views and name not in menu_names:
            if not sm.find_permissions_view_menu(view_menu):
                sm.del_view_menu(name)
                removed["views"] += 1
                log.info("pruned stale view menu %s", name)

    for permission in list(sm.session.query(sm.permission_model).all()):
        referenced = sm.session.query(sm.permissionview_model).filter_by(
            permission_id=permission.id
        ).count()
        if not referenced:
            sm.del_permission(permission.name)
            removed["permissions"] += 1
            log.info("pruned unused permission %s", permission.name)

    sm.session.commit()
    return removed
