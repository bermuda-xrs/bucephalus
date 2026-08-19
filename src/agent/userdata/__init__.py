"""User-authored content: eval suites, reviewer policy, grounding notes.

Everything in here is written by a person through the UI, not by editing a
file on disk. Files remain an import path so existing suites are not stranded.
"""

from .store import (  # noqa: F401
    DEFAULT_POLICY,
    Suite,
    SuiteTask,
    UserStore,
)
