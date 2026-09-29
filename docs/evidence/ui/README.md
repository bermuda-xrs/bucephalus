# The console, verified visually - 2026-09-28 (iteration 3, 4.7)

**How these were made.** A scratch control plane with mock models and scratch
data directories, seeded through the product's own APIs with: a drafted plan
with open questions and an irreversible step; a follow-up task parked on a
`write_file` approval; a finished task with its answer; a long run with a tool
trail (one call failed); and, on a second plane, a record whose first event
was edited after it was written. Desktop shots: headless Edge at 1440x900.
Phone shots: Edge over the DevTools protocol with a true 390x844 mobile
viewport at 2x (desktop headless will not lay out narrower than ~500 px from
a window size, so a plain `--window-size=390` shot is a cropped desktop).

| state | dark | light |
|---|---|---|
| Plan for signature | `plan-card-dark-{desktop,phone}.png` | `plan-card-light-*` |
| Parked approval (amber, inline, and in Needs you) | `parked-approval-dark-*` | `parked-approval-light-*` |
| Long run: tool cards with receipts, context ring at 22k of 56k | `long-running-dark-*` | `long-running-light-*` |
| Finished, with its answer | `answer-dark-*` | `answer-light-*` |
| Empty state | `empty-dark-*` | `empty-light-*` |
| Control Center: Memory, Integrity, Settings | `control-*-dark-*` | `control-*-light-*` |
| **A tampered chain** - banner and Integrity pane in the reserved colour | `tampered-*-dark-*` | `tampered-*-light-*` |

**Also clicked through, not only photographed:** a message drafts a plan, the
plan is signed, the task runs and its answer appears; a suite is created from
the Control Center; the palette opens on Ctrl+K, filters, runs a command.

## Contrast (WCAG 2.1 AA, 4.5:1 for text)

Every text/background pair in both palettes, computed from the tokens in
`src/agent/control/ui.css`:

| pair | dark | light |
|---|---|---|
| text on ground / panel / raised | 14.63 / 13.76 / 12.45 | 15.12 / 16.37 / 14.42 |
| secondary text on ground / panel / raised | 6.49 / 6.11 / 5.53 | 5.88 / 6.37 / 5.61 |
| accent on panel; text on accent | 7.82; 8.40 | 6.02; 6.02 |
| amber (needs you) on panel; text on amber | 7.91; 8.49 | 6.04; 6.04 |
| done on panel | 8.32 | 5.61 |
| integrity red on panel / ground | 5.39 / 5.73 | 6.03 / 5.57 |

All pass AA; the lowest is 5.39:1.

## Keyboard

Every control is a native button, input or textarea, with a visible focus
ring. Checked in a browser: Ctrl/Cmd+K opens the palette with focus in it;
arrows and Enter run a command; Tab and Shift+Tab stay inside an open dialog
(palette, Control Center); Escape closes it and focus returns to where it
was; Ctrl+Enter sends from the composer; a skip link leads to the composer.

## Known limits

- The phone plan-card shot shows the END of the card (signing, then the
  follow-up), because opening a conversation scrolls to its newest item; the
  desktop shot shows the card from the top.
- Screen-reader use was not tested with an actual screen reader, only by the
  accessibility tree (every control has a name).

## Added 2026-09-29: the frozen app and the TUI

- `frozen-app-task-done.png` - the rebuilt desktop app's own window (4.8),
  after a task typed into its composer, drafted, signed through the plan
  card and answered; captured over DevTools from the WebView2. Mock models.
- `tui-plan-drafted.png`, `tui-signed-and-done.png` - the terminal UI through
  Textual's pilot at 140x44: the plan awaiting `s`, then signed and done.
  **These found D82:** the first pair showed neither the input row nor the
  status bar - the tabs took every row and the bar was docked under the key
  bar - while every TUI test passed, because the tests set the input's value
  and read the bar's text without looking. Fixed, and
  `test_the_input_and_the_status_bar_are_on_screen` checks the regions.
