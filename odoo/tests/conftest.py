"""Load the Odoo-free modules of flight_deck without importing Odoo.

The models package's __init__ imports every Odoo model, so the modules are
loaded into a stand-in package that points at the same folder.
"""
import importlib
import os
import sys
import types

MODELS = os.path.join(os.path.dirname(__file__), "..", "flight_deck", "models")

if "fdm" not in sys.modules:
    pkg = types.ModuleType("fdm")
    pkg.__path__ = [os.path.abspath(MODELS)]
    sys.modules["fdm"] = pkg


def load(name):
    return importlib.import_module("fdm." + name)
