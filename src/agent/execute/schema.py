from __future__ import annotations


def build_action_schema(tools, code_as_action: bool) -> dict:
    branches = [
        {
            "type": "object",
            "properties": {"kind": {"const": "action"}, "tool": {"const": name},
                           "args": tools.get(name).schema},
            "required": ["kind", "tool", "args"],
        }
        for name in tools.names()
    ]
    if code_as_action:
        branches.append({
            "type": "object",
            "properties": {"kind": {"const": "code"}, "code": {"type": "string"}},
            "required": ["kind", "code"],
        })
    branches.append({
        "type": "object",
        "properties": {"kind": {"const": "final"}, "answer": {}},
        "required": ["kind", "answer"],
    })
    return {"anyOf": branches}
