"""Import all node modules so their registry.register(...) side-effects run.
Called once at app startup and at test setup, so the node registry is
populated before any flow is run."""
def load_all():
    from flightdeck.hub.nodes import http, odoo_xmlrpc, basic  # noqa: F401
