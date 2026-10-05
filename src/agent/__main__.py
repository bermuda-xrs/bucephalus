from agent.frozen import child_entry

child_entry()

from agent.cli import main  # noqa: E402

raise SystemExit(main())
