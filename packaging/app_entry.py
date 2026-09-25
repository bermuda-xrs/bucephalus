"""The desktop app's frozen entry. The D13 dispatch comes before anything
else is imported: this process may be a sandbox child (agent.frozen)."""

from agent.frozen import child_entry

child_entry()

from agent.desktop import main  # noqa: E402

raise SystemExit(main())
