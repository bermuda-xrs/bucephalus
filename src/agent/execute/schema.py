from __future__ import annotations


LONG_ARGS = {"write_file": "content", "apply_patch": "patch", "edit_lines": "content"}


def _without(schema: dict, arg: str) -> dict:
    return {**schema, "properties": {k: v for k, v in schema.get("properties", {}).items() if k != arg},
            "required": [r for r in schema.get("required", []) if r != arg]}


def build_action_schema(tools, code_as_action: bool, allow_final: bool = True, thought: int = 0, carry: int = 0,
                        text_out: bool = False, names: dict | None = None) -> dict:
    names = names or {}

    def branch(props: dict, required: list, acts: bool) -> dict:
        p, r = ({"thought": {"type": "string", "maxLength": thought}}, ["thought"]) if thought else ({}, [])
        p.update(props)
        r += required
        if carry and acts:
            p["carry"] = {"type": "string", "maxLength": carry}
            r.append("carry")
        return {"type": "object", "properties": p, "required": r}

    branches = []
    for name in tools.names():
        args = tools.get(name).schema
        if text_out and name in LONG_ARGS:
            args = _without(args, LONG_ARGS[name])
        branches.append(branch({"kind": {"const": "action"}, "tool": {"const": names.get(name, name)}, "args": args},
                               ["kind", "tool", "args"], True))
    if code_as_action:
        props = {"kind": {"const": "code"}} if text_out else {"kind": {"const": "code"}, "code": {"type": "string"}}
        branches.append(branch(props, list(props), True))
    if allow_final:
        branches.append(branch({"kind": {"const": "final"}, "answer": {}}, ["kind", "answer"], False))
    return {"anyOf": branches}
