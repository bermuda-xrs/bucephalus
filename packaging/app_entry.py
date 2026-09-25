"""The desktop app's frozen entry. The D13 dispatch comes before anything
else is imported: this process may be a sandbox child (agent.frozen)."""

from agent.frozen import child_entry

child_entry()

import os  # noqa: E402
import sys  # noqa: E402

from agent.desktop import main  # noqa: E402

rc = main()
# Everything this app owns is finished when main() returns: the server is
# stopped, every child killed, the database closed. What remained was ~6.4 s of
# interpreter finalisation inside pythonnet/WebView2 after the window closed
# (measured, Phase F), with no thread of ours waiting. Flush and leave.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.flush()
    except Exception:  # noqa: BLE001 - a closed or absent stream has nothing to flush
        pass
os._exit(rc)
