"""Base-URL presets for known local services (reached via host.docker.internal)."""
import os


def presets():
    host = os.environ.get("TOKEN_AUDIT_HUB_HOST", "host.docker.internal")
    return [
        {"key": "odoo", "label": "Odoo", "baseUrl": "http://%s:8069" % host},
        {"key": "odoo-shim", "label": "Odoo CE shim", "baseUrl": "http://%s:8179" % host},
        {"key": "discount", "label": "Discount Service", "baseUrl": "http://%s:8100" % host},
        {"key": "gorules", "label": "GoRules", "baseUrl": "http://%s:3100" % host},
        {"key": "flightdeck", "label": "FlightDeck", "baseUrl": "http://%s:8010" % host},
    ]
