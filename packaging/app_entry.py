from agent.frozen import child_entry

child_entry()

import os  # noqa: E402
import sys  # noqa: E402

from agent.desktop import main  # noqa: E402

rc = main()
for stream in (sys.stdout, sys.stderr):
    try:
        stream.flush()
    except Exception:  # noqa: BLE001
        pass
os._exit(rc)
