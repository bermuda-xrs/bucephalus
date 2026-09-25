"""`python -m agent`, and the entry script any frozen build of the CLI uses.

The D13 dispatch comes first, before the CLI is even imported: in a frozen
build this process may be a sandbox child, which must run the model's code
and nothing of the application (agent.frozen).
"""

from agent.frozen import child_entry

child_entry()

from agent.cli import main  # noqa: E402

raise SystemExit(main())
