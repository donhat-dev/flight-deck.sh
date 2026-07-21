from typing import Dict, List
from flightdeck.hub.types import NodeDef

_REGISTRY: Dict[str, NodeDef] = {}

def register(nodedef: NodeDef) -> None:
    _REGISTRY[nodedef.type] = nodedef

def get(type: str) -> NodeDef:
    return _REGISTRY[type]

def all_defs() -> List[NodeDef]:
    return list(_REGISTRY.values())
