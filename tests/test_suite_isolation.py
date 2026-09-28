"""No test touches the operator's real user directories.

FOUND 2026-09-28: the operator's real tier-1 memory held
"- [user] injected instruction", written 2026-09-25 by the audit's probe of
F6. `AgentContext` keeps memory in `user_data_dir()/memory`, and the suite
redirected only the CONFIG dir, so any in-process context wrote the real
memory - which goes into the prefix of every real session afterwards.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

from agent.paths import APP, user_data_dir

REAL_DATA = Path(platformdirs.PlatformDirs(APP).user_data_dir)


def test_the_user_data_dir_is_scratch_during_tests():
    assert REAL_DATA not in (user_data_dir(), *user_data_dir().parents)


def test_a_context_never_opens_the_real_memory(ctx):
    root = Path(ctx.memory.root).resolve()
    assert REAL_DATA.resolve() not in (root, *root.parents), root
