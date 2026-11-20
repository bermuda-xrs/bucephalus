from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from test_console_pages import _pane
from test_console_reskin_guards import _routes, UI

NODE = shutil.which("node")


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


@pytest.fixture
def tid(ctx, workspace):
    (workspace / "a.py").write_bytes(b"x = 1\n")
    return create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60)).id


def test_the_workspace_says_where_it_is_on_disk(ctx, api, tid):
    root = Path(ctx.config.root)
    d = api.get(f"/tasks/{tid}/files?path=work").json()
    assert Path(d["abs"]) == (root / "work").resolve() or Path(d["abs"]) == root / "work"
    f = api.get(f"/tasks/{tid}/files?path=work/a.py").json()
    assert Path(f["abs"]).is_absolute() and Path(f["abs"]).read_text() == "x = 1\n"


def _js(script: str):
    if NODE is None:
        pytest.skip("node is not installed")
    src = UI[UI.index("const esc ="):UI.index("const args =")]
    src += UI[UI.index("const HL_KW"):UI.index("function ideReset(")]
    out = subprocess.run([NODE, "-e", src + script], capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


SAMPLES = [
    ("a.py", 'def f(x):\n    """doc <b>\nmore"""\n    return "<i>" + \'it\'  # note <script>\nif x: pass\n'),
    ("a.js", "const a = `t<${b}>`; // c <x>\n/* block\n<y> */ if (a) return 'q';\n"),
    ("a.json", '{"k": "<v>", "n": true}\n'),
    ("a.css", "a { color: red; } /* <c> */\n"),
    ("a.toml", "# c\nname = \"x<y>\"\n"),
    ("a.txt", "plain <tag> & 'quote'\n"),
    ("noext", "<b>"),
]


def test_highlighting_escapes_everything_and_loses_nothing():
    out = _js(f"process.stdout.write(JSON.stringify({json.dumps(SAMPLES)}.map(([p, t]) => hl(t, p))));")
    for (path, text), got in zip(SAMPLES, out):
        tags = re.findall(r"<[^>]*>", got)
        assert all(re.fullmatch(r'<span class="tk-[kcs]">|</span>', t) for t in tags), (path, tags)
        assert html.unescape(re.sub(r"<[^>]*>", "", got)) == text, path


def test_highlighting_marks_keywords_strings_and_comments():
    py, js = _js(f"process.stdout.write(JSON.stringify([hl({json.dumps(SAMPLES[0][1])}, 'a.py'), "
                 f"hl({json.dumps(SAMPLES[1][1])}, 'a.js')]));")
    assert '<span class="tk-k">def</span>' in py and '<span class="tk-k">return</span>' in py
    assert '<span class="tk-s">&quot;&quot;&quot;doc &lt;b&gt;\nmore&quot;&quot;&quot;</span>' in py
    assert '<span class="tk-c"># note &lt;script&gt;</span>' in py
    assert '<span class="tk-c">/* block\n&lt;y&gt; */</span>' in js and '<span class="tk-k">const</span>' in js


def test_the_gutter_numbers_every_line():
    n = _js("const v = codeView('a\\nb\\n\\nc', 'x.py'); process.stdout.write(JSON.stringify(v.match(/class=\"gut\"[^>]*>([^<]*)</)[1]));")
    assert n == "1\n2\n3\n4"


def test_open_in_cursor_points_at_the_file():
    url = _js("process.stdout.write(JSON.stringify(cursorUrl('C:\\\\Users\\\\Βου\\\\a b.py')));")
    assert url == "cursor://file/C:/Users/%CE%92%CE%BF%CF%85/a%20b.py"


def test_a_new_file_is_still_a_drop_and_an_edit_names_the_hash_it_replaces():
    assert {r for r in _routes() if r[1].startswith("/tasks/{}/files") and r[0] in ("PUT", "PATCH")} == {
        ("PUT", "/tasks/{}/files")}
    save = UI[UI.index("async function ideSave("):]
    save = save[:save.index("\n}\n")]
    assert "post(`/tasks/${S.filesTask}/files`" in save
    edit = UI[UI.index("async function ideEditSave("):]
    edit = edit[:edit.index("\n}\n")]
    assert 'method: "PUT"' in edit and "base_sha256" in edit


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _put(api, tid, path, text, base):
    return api.put(f"/tasks/{tid}/files", json={"path": path, "text": text, "base_sha256": base})


def test_a_text_file_says_its_hash(api, tid):
    assert api.get(f"/tasks/{tid}/files?path=work/a.py").json()["sha256"] == _sha(b"x = 1\n")


def test_editing_in_place_overwrites_keeps_the_old_text_and_tells_the_agent(ctx, api, tid, workspace):
    from agent.ledger.artifacts import read_artifact

    r = _put(api, tid, "work/a.py", "x = 2\r\n", _sha(b"x = 1\n"))
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == _sha(b"x = 2\r\n") and (workspace / "a.py").read_bytes() == b"x = 2\r\n"
    ev = [e["payload"] for e in ctx.log.events(tid) if e["type"] == "human.edited"]
    assert len(ev) == 1 and ev[0]["path"] == "work/a.py"
    assert (ev[0]["before_sha256"], ev[0]["after_sha256"]) == (_sha(b"x = 1\n"), _sha(b"x = 2\r\n"))
    old = read_artifact(ctx.conn, ev[0]["before_artifact"])
    assert old["content"] == "x = 1\n" and old["taint"].value == "untrusted"
    said = [e["payload"]["text"] for e in ctx.log.events(tid) if e["type"] == "human.message"]
    assert len(said) == 1 and "a.py" not in said[0]  # a file name may come from untrusted text: never in a user message


def test_an_edit_never_writes_through_a_link(ctx, api, tid, workspace):
    import os

    target = workspace / "real.txt"
    target.write_bytes(b"r")
    try:
        os.symlink(target, workspace / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make a symlink")
    assert _put(api, tid, "work/link.txt", "p", _sha(b"r")).status_code == 403
    assert target.read_bytes() == b"r" and (workspace / "link.txt").is_symlink()


def test_an_edit_on_a_changed_file_is_refused_and_changes_nothing(api, tid, workspace):
    assert _put(api, tid, "work/a.py", "x = 2\n", _sha(b"x = 0\n")).status_code == 409
    assert (workspace / "a.py").read_bytes() == b"x = 1\n"


def test_a_running_task_or_a_step_in_progress_refuses_edits(ctx, api, tid, workspace):
    from agent.kernel.tasks import load_task, set_status
    from agent.kernel.types import TaskStatus
    from agent.runlock import task_lock

    with task_lock(ctx, tid):
        assert _put(api, tid, "work/a.py", "x = 2\n", _sha(b"x = 1\n")).status_code == 409
    set_status(ctx.conn, ctx.log, load_task(ctx.conn, tid), TaskStatus.RUNNING, "test")
    assert _put(api, tid, "work/a.py", "x = 2\n", _sha(b"x = 1\n")).status_code == 409
    assert (workspace / "a.py").read_bytes() == b"x = 1\n"


def test_an_edit_stays_in_scope_and_only_changes_an_existing_text_file(ctx, api, tid, workspace):
    root = Path(ctx.config.root)
    (root / "outside.txt").write_bytes(b"o")
    assert _put(api, tid, "outside.txt", "p", _sha(b"o")).status_code == 403
    assert (root / "outside.txt").read_bytes() == b"o"
    assert _put(api, tid, "work/none.py", "p", _sha(b"")).status_code == 404
    assert not (workspace / "none.py").exists()
    (workspace / "b.bin").write_bytes(b"\xff\xfe\x00")
    assert _put(api, tid, "work/b.bin", "p", _sha(b"\xff\xfe\x00")).status_code == 403
    assert (workspace / "b.bin").read_bytes() == b"\xff\xfe\x00"


def test_the_files_page_is_an_explorer():
    page = _pane("files")
    assert 'id="treePg"' in page and 'id="edPg"' in page and "ideNew" in page
    for act in ("ideDir", "ideFile", "ideTab", "ideClose", "ideNew", "ideCopy", "ideSave", "ideSource", "ideEdit",
                "ideEditCancel", "ideEditSave", "ideUndo"):
        assert act in UI[UI.index("const ACTIONS = {"):], act
