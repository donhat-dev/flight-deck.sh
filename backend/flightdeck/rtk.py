"""Best-effort parser for `rtk gain` CLI output (token savings at the CLI layer)."""
import re
import shutil
import subprocess


def _num(s: str) -> int:
    s = s.strip().replace(",", "")
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    return int(round(float(s) * mult))


def parse_gain(text: str) -> dict:
    out = {"tokens_saved": 0, "commands": 0}
    m = re.search(r"Tokens saved:\s*([\d.,]+[KM]?)", text)
    if m:
        out["tokens_saved"] = _num(m.group(1))
    m = re.search(r"Total commands:\s*([\d.,]+[KM]?)", text)
    if m:
        out["commands"] = _num(m.group(1))
    return out


def rtk_savings_usd(rate_per_mtok: float = 5.0) -> float:
    if not shutil.which("rtk"):
        return 0.0
    try:
        out = subprocess.run(
            ["rtk", "gain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if out.returncode != 0:
            return 0.0
        return parse_gain(out.stdout)["tokens_saved"] * rate_per_mtok / 1_000_000
    except Exception:
        return 0.0
