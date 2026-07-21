"""Expression evaluation + templating. ZEN expression language via zen-engine.

ZEN references the context root with `$` (e.g. `$json.id`). We pass the context
dict straight through; `evaluate` is a thin wrapper so the rest of the engine
never imports zen directly (swappable for the sandbox fallback)."""
import re
from typing import Any, Dict, List, Optional
import zen

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


def build_context(current_item: Optional[dict], node_outputs: Dict[str, dict],
                  vars: Dict[str, Any], run_meta: Dict[str, Any]) -> dict:
    return {
        "$json": (current_item or {}).get("json", {}),
        "$node": node_outputs,   # {nodeName: {"json": {...}}} first item per node
        "$vars": vars,
        "$run": run_meta,
    }


def evaluate(expression: str, context: dict) -> Any:
    return zen.evaluate_expression(expression.strip(), context)


def _fmt(v: Any) -> str:
    # ZEN returns floats for integer arithmetic (e.g. 7 -> 7.0). For embedded
    # substitution (URLs/ids/strings) render integral floats without the
    # trailing ".0"; everything else stringifies normally.
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def interpolate(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        m = _EXPR.fullmatch(value.strip())
        if m:                                   # whole string is one expression
            return evaluate(m.group(1), context)
        return _EXPR.sub(lambda x: _fmt(evaluate(x.group(1), context)), value)
    if isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, context) for v in value]
    return value
