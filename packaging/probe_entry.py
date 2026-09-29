"""The D13 gate: the sandbox's own property tests, run FROM a frozen binary.

Not a re-implementation of the checks - the same test files, bundled, so a
property that holds unfrozen and fails frozen shows up as the same red test.
"""

from agent.frozen import child_entry

child_entry()                      # first: a sandbox child never gets further

import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

GATE = [
    "test_frozen_sandbox.py",      # interpreter not app, no recursion, timeout
    "test_sandbox_limits.py",      # the Job Object: memory, CPU, processes
    "test_m5_action.py",           # scope refusals, the tool bridge and gate
    "test_sqlite_routes.py",       # the SQLite authorizer, file: URI spellings
    "test_sandbox_libraries.py",   # sympy inside the sandbox
    "test_sandbox_network_fence.py",  # no sockets from the sandbox, loopback included
    "test_junction_escape.py",     # junctions and stream names, gate and sandbox (D61, D68)
]

if __name__ == "__main__":
    tests = os.path.join(sys._MEIPASS, "tests")  # noqa: SLF001 - PyInstaller's bundle dir
    args = sys.argv[1:]
    # Flags alone must not drop the gate: pytest would then collect from the
    # working directory instead.
    if not any(not a.startswith("-") for a in args):
        args += [os.path.join(tests, t) for t in GATE]
    sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", tests,
                          "-o", "addopts=", *args]))
