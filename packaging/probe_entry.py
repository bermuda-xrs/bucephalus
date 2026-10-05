from agent.frozen import child_entry

child_entry()

import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

GATE = [
    "test_frozen_sandbox.py",
    "test_sandbox_limits.py",
    "test_m5_action.py",
    "test_sqlite_routes.py",
    "test_sandbox_libraries.py",
    "test_sandbox_network_fence.py",
    "test_junction_escape.py",
]

if __name__ == "__main__":
    tests = os.path.join(sys._MEIPASS, "tests")  # noqa: SLF001
    args = sys.argv[1:]
    if not any(not a.startswith("-") for a in args):
        args += [os.path.join(tests, t) for t in GATE]
    sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", tests,
                          "-o", "addopts=", *args]))
