"""Core Hub types. Python 3.7 compatible (no PEP 604 unions)."""
from typing import Any, Callable, Dict, List, Optional

Item = Dict[str, Any]          # {"json": {...}}
NodeOutput = List[List[Item]]  # outputs[socketIndex] -> items

def item(json_dict: Optional[dict] = None) -> Item:
    return {"json": dict(json_dict or {})}

class NodeDef:
    def __init__(self, type: str, label: str, inputs: List[str],
                 outputs: List[str], run: Callable, params_schema: Optional[dict] = None):
        self.type = type
        self.label = label
        self.inputs = inputs
        self.outputs = outputs
        self.run = run
        self.params_schema = params_schema or {}
