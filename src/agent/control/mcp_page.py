from __future__ import annotations


def listing(ctx) -> dict:
    return {"running": bool(ctx.mcp_servers),
            "servers": [{"name": n, "argv": list(c.argv), "env": sorted(c.env), "read_only": list(c.read_only),
                         "strict_paths": c.strict_paths, "timeout_s": c.timeout_s, "start_timeout_s": c.start_timeout_s}
                        for n, c in ctx.config.mcp_servers.items()]}


def test(ctx, name: str) -> dict:
    from ..execute.mcp import McpServer

    cfg = ctx.config.mcp_servers[name]
    server = McpServer(name, cfg, str(ctx.config.root))
    try:
        server.start()
        tools = server.list_tools()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tools": [], "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        server.close()
    return {"ok": True, "error": "",
            "tools": [{"name": str(t.get("name", ""))[:80], "description": str(t.get("description", ""))[:600],
                       "writes": str(t.get("name", "")) not in cfg.read_only} for t in tools]}
