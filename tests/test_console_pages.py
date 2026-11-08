from __future__ import annotations

import re

from test_console_reskin_guards import HTML, UI, _calls


def _pane(name: str) -> str:
    block = UI[UI.index("const PANES = {"):]
    i = block.index(f"async {name}()")
    j = block.find("\n  async ", i + 1)
    return block[i:j if j > 0 else block.index("\n};")]


def test_the_nav_offers_sessions_files_and_models():
    for name in ("sessions", "files", "models"):
        assert f'data-pane="{name}"' in HTML, name
        _pane(name)


def test_sessions_lists_active_and_archived_conversations():
    page = _pane("sessions")
    assert "/conversations?" in page and "archived=" in page and "q=" in page
    for act in ("selectConversation", "patchConversation", "exportOne"):
        assert f'data-action="{act}"' in page, act


def test_files_browses_any_task_workspace_and_takes_drops():
    page = _pane("files")
    assert 'api("/tasks' in page and "pickWorkspace" in page and 'class="drop' in page


def test_models_offers_every_model_route_the_server_has():
    page = _pane("models")
    assert 'api("/models")' in page
    for act in ("useModel", "loadModel", "unloadModel"):
        assert f'data-action="{act}"' in page, act
    used = _calls(UI)
    for path in ("/models/select", "/models/run", "/models/unload"):
        assert ("POST", path) in used, path


def test_models_never_offers_controls_for_your_own_server():
    page = _pane("models")
    assert re.search(r"managed\s*\?", page), "a server we do not manage must get no load, unload or use buttons"
