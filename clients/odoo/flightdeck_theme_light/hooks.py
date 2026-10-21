def collapse_sidebars(env):
    """Start every existing account on the narrow rail.

    The default above only reaches accounts created after this module, and an
    instance that has been used already has all of its accounts on the wide
    one. Anybody who wants it back says so once, and the setting is theirs.
    """
    users = env["res.users"].search([("sidebar_type", "=", "large")])
    users.write({"sidebar_type": "small"})
