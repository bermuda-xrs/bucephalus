"""A4 — where a task came from, and what that costs it.

A message channel is the first entry point where the person typing is not
necessarily the person who owns the machine. Everything else — the CLI, the
console, the TUI — is reached by someone already sitting at the keyboard. A
bot is reachable by anyone who can find it.

THE STRUCTURAL DECISION, AND THE ONE WORTH ARGUING ABOUT.

Origin is stored ON THE TASK ROW, and the restrictions are DERIVED FROM IT at
the moment they are checked. The obvious alternative — have the bot call
`ctx.restrict_tools()` when it creates the task — is what Phase 5 already
provides and it is wrong here, because `_tool_policy` is a dict in process
memory and the task row is durable. Restart the process and every parked
channel task comes back UNRESTRICTED, with nothing in the record saying it
used to be otherwise. The task outlives the control.

That is DR1 with the scopes swapped: not a key too wide, but a lifetime too
short. A control whose scope is smaller than what it protects has a window,
and here the window is "any time the process restarts", which for a parked
task waiting on a human is most of its life.

Storing the ORIGIN rather than the POLICY is deliberate too. Origin is a
fact — this arrived over Telegram — and facts do not go stale. A stored
policy is a rule frozen at creation time, so tightening the rule later would
leave every existing channel task on the old one, and the tasks most likely
to be sitting parked in the database are the oldest ones.

WHY DENY `run_code` WHEN THE TAINT CEILING ALREADY EXISTS.

It does not already exist, and this is the gap worth naming. Untrusted
arguments cannot reach a USER-ceiling tool, so a message that says
`run_code("...")` is refused on taint. But the model can WRITE ITS OWN CODE,
in its own words, quoting nothing — and that argument is USER taint, because
it is model output derived from the goal. It passes the ceiling. The taint
wall stops the attacker's bytes; it does not stop the attacker's INTENT
laundered through the model's paraphrase. Exposure is the wall for that one,
and it is the reason this list is not just a restatement of the taint table.
"""

from __future__ import annotations

from .exposure import UNRESTRICTED, ToolPolicy

LOCAL = "local"
TELEGRAM = "telegram"

# Origins reachable by someone who is not at the keyboard.
CHANNEL_ORIGINS = frozenset({TELEGRAM})

# A3. A task whose trigger is a timer. Its goal was written by the owner, so
# its bytes are not a stranger's - what changes at 3am is that NOBODY IS
# WATCHING.
SCHEDULE = "schedule"

# Origins with nobody present when the task runs: a channel task's author is
# not at this keyboard, a scheduled task's author is asleep. Neither may
# auto-proceed - no deputy, no scoped-write fast path - because both remove the
# permission layer for exactly the tasks running with nobody there (11.2).
UNATTENDED_ORIGINS = CHANNEL_ORIGINS | {SCHEDULE}

# What a scheduled task loses (11.6). NOT the channel's list, because the risk
# is not a stranger's instruction; it is that nothing done tonight is looked at
# until morning. So the floor removes what cannot be undone or reviewed after
# the fact:
#
#   git_push            outward and irreversible
#   append_to_outbox    an egress path addressed by the model
#   memory_write        tier 1 is in every future prompt; changing what the
#   memory_consolidate  agent believes while nobody watches is not routine work
#   delegate            unbounded fan-out from a timer
#
# `run_code` is NOT here. A scheduled report may need to compute, and a
# side-effecting call from an unattended task parks for a human anyway.
SCHEDULE_DENIED = frozenset({
    "git_push",
    "append_to_outbox",
    "memory_write",
    "memory_consolidate",
    "delegate",
})

# What a channel-originated task loses. Each line is a reason, not a list.
#
#   run_code            see the module docstring: the taint ceiling does not
#                       cover model-authored code, and a chat message that
#                       talks the model into writing a script is the whole
#                       attack in one step.
#   git_push            already TRUSTED tier and already parks. Denied as
#                       well because a wall you can see is worth more than a
#                       wall you have to reason about, and because the tier
#                       lives on the tool while this lives on the task.
#   append_to_outbox    the channel is already an egress path. A second one,
#                       addressed by the model, from a task whose goal was
#                       written by a stranger, is how a chat message becomes
#                       an email to somebody else.
#   memory_write        tier 1 is in the prefix of EVERY future session.
#   memory_consolidate  A message must never be able to write itself into
#                       what the agent believes forever — this is the A1/A4
#                       composition, the same shape 4.6 checked for A2.
#   delegate            unbounded depth from an unbounded number of senders.
#                       No tool yet; named so one added later inherits it.
CHANNEL_DENIED = frozenset({
    "run_code",
    "git_push",
    "append_to_outbox",
    "memory_write",
    "memory_consolidate",
    "delegate",
})

# A channel task's ceiling, as a fraction of the local default. Not a separate
# absolute number: two knobs that must be kept in a relationship drift apart,
# and the relationship — "a stranger gets less than I do" — is the thing worth
# stating.
CHANNEL_BUDGET_FRACTION = 0.2


def is_channel(origin: str | None) -> bool:
    return (origin or LOCAL) in CHANNEL_ORIGINS


def is_unattended(origin: str | None) -> bool:
    return (origin or LOCAL) in UNATTENDED_ORIGINS


def policy_for(origin: str | None) -> ToolPolicy:
    """The floor for this origin. Unioned with any narrower policy on read."""
    if (origin or LOCAL) == SCHEDULE:
        return ToolPolicy(denied=SCHEDULE_DENIED, reason="scheduled task, unattended")
    if not is_channel(origin):
        # The SINGLETON, not an equal copy. A fresh `ToolPolicy()` compares
        # equal and would pass every test that uses `==`; the identity is what
        # makes "this agent is unrestricted" checkable at a glance in a
        # debugger and in the one test that asserts it.
        return UNRESTRICTED
    return ToolPolicy(denied=CHANNEL_DENIED, reason=f"{origin} channel task")


def clamp_budget(budget, limits, origin: str | None):
    """A channel task gets a fraction of a local task's ceiling.

    Applied at creation, so the clamped number is what lands in `budget_json`
    and a re-run after a restart reads the clamped value rather than
    re-deriving it. Same reasoning as the origin column: the durable thing has
    to carry the constraint.
    """
    if not is_channel(origin):
        return budget
    ceiling_usd = limits.default_task_budget_usd * CHANNEL_BUDGET_FRACTION
    ceiling_s = limits.default_task_budget_seconds * CHANNEL_BUDGET_FRACTION
    return budget.model_copy(update={
        "usd": min(budget.usd, ceiling_usd),
        "seconds": min(budget.seconds, int(ceiling_s)),
    })
