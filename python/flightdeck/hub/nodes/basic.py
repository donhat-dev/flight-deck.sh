"""Start, Set/Transform, Condition nodes."""
from typing import List
from flightdeck.hub import registry, expr
from flightdeck.hub.types import NodeDef, Item, item

def start_execute(params, inputs, ctx) -> List[List[Item]]:
    return [[item(params.get("seed", {}))]]

def set_execute(params, inputs, ctx) -> List[List[Item]]:
    out = []
    for it in (inputs[0] if inputs and inputs[0] else [item({})]):
        context = expr.build_context(it, ctx.node_outputs, ctx.vars, ctx.run_meta)
        merged = dict(it["json"])
        for a in params.get("assignments", []):
            val = expr.evaluate(a["expression"], context)
            merged[a["name"]] = val
            ctx.vars[a["name"]] = val
        out.append(item(merged))
    return [out]

def condition_execute(params, inputs, ctx) -> List[List[Item]]:
    truthy, falsy = [], []
    for it in (inputs[0] if inputs and inputs[0] else []):
        context = expr.build_context(it, ctx.node_outputs, ctx.vars, ctx.run_meta)
        (truthy if expr.evaluate(params["expression"], context) else falsy).append(it)
    return [truthy, falsy]

registry.register(NodeDef("start", "Start", [], ["main"], run=start_execute,
                          params_schema={"seed": "json"}))
registry.register(NodeDef("set", "Set / Transform", ["main"], ["main"], run=set_execute,
                          params_schema={"assignments": "list"}))
registry.register(NodeDef("condition", "Condition", ["main"], ["true", "false"],
                          run=condition_execute, params_schema={"expression": "string"}))
