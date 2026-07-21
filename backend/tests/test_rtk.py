from flightdeck import rtk

SAMPLE = """RTK Token Savings (Global Scope)
Total commands:    1155
Input tokens:      809.2K
Tokens saved:      562.1K (69.5%)
"""


def test_parse_gain_extracts_tokens_saved():
    g = rtk.parse_gain(SAMPLE)
    assert g["tokens_saved"] == 562_100
    assert g["commands"] == 1155


def test_parse_gain_missing_defaults_zero():
    g = rtk.parse_gain("nothing here")
    assert g == {"tokens_saved": 0, "commands": 0}


def test_rtk_savings_usd_no_binary(monkeypatch):
    monkeypatch.setattr(rtk.shutil, "which", lambda name: None)
    assert rtk.rtk_savings_usd() == 0.0
