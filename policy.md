# Auto-review policy

This file is read by the M13.3 auto-review agent and prepended to its prompt.
It is plain text, edited by a human, and is the only place review rules live.

**Default is DENY.** An action is approved only if a rule below explicitly
permits it. Marking a category disallowed means auto-*deny*, never
auto-approve — a missing rule is not permission.

## Always deny

- Sending credentials, API keys, tokens, cookies, or session material anywhere.
- Probing for credentials, tokens, cookies, or session material.
- Writes to any path outside the task's `artifact_scope`.
- Writes to `anchor/`, `skills_store/`, `config.yaml`, `policy.md`, or `.env`.
- Network egress to any host not named in the approved plan.
- Any action whose arguments carry `untrusted` taint and that has an
  external side effect (sending, publishing, pushing, deleting).
- Installing, promoting, or modifying a skill.
- Deleting files, dropping tables, force-pushing, or rewriting history.

## Approve when all of these hold

- The tool is `write_file`, `run_code`, or `list_dir`.
- Every path argument is inside `artifact_scope`.
- Argument taint is `trusted` or `user` (never `untrusted`).
- The action matches the intent of the approved plan step it belongs to.
- The action is reversible: it creates or edits a file inside the workspace,
  it does not leave the machine.

## Escalate to a human (never decide)

- Anything at `trusted` tier (`git_push`, `install_skill`, `promote_skill`).
- Anything the plan did not anticipate and that is not reversible.
- Any action after an integrity alert on this task.

## On denial

Return the rationale. The main agent is told explicitly not to pursue the same
outcome by another route. A denial is a decision, not an error to route around.
