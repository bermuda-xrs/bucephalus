"""A8 — a terminal surface.

7.3, ANSWERED: the TUI imports the kernel directly AND renders through the
control plane's event vocabulary. That is not a compromise between the two
options; it is the observation that they were answering different questions.

The question worth asking is not "HTTP or imports" but "what is the contract".
Making the TUI speak HTTP would mean every TUI user runs a server — a bound
port, gated by `ALPHA_ALLOW_REMOTE` and about to have a message channel in
front of it — to look at a task on their own machine. That is a real security
surface bought for a maintenance benefit.

But the maintenance benefit is real, and it does not actually come from the
transport. It comes from the two surfaces agreeing on what an event MEANS. So
the TUI renders from `control.events.to_client_event`, the same mapping the
SSE endpoint uses. TUI and web console cannot drift on vocabulary, a bug in
the mapping shows up in both, and neither has to bind a port.

That also inherits a security property rather than reimplementing it:
`to_client_event` refuses to emit `monitor.*`, so the out-of-band monitor
stays invisible to this surface for free. A TUI reading the raw event log
would have had to remember, and forgetting is how M8.3 gets undone.

`textual` is the one new dependency, Python-only, no new runtime.
"""

from .theme import banner_text, load, supports_glyphs

__all__ = ["banner_text", "load", "supports_glyphs"]
