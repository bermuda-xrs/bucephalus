# Your own task suite

The four files here are **examples of the shape**, not a benchmark. The suite
that matters is the one you write for your own domain — it is the only one
that can measure skill lift on the work you actually do, and it is the
denominator of every number this system reports.

Aim for **30 tasks**. Below that, `gate.min_paired_trials` will report
`insufficient` and refuse to promote anything, which is the gate working
correctly rather than a bug to route around.

Each task is one JSON file:

```json
{
  "key": "unique-stable-id",
  "goal": "what the agent must accomplish",
  "success_criterion": "a verifier registered in the mesh",
  "output_schema": { "type": "object", "required": ["..."] },
  "artifact_scope": ["work"],
  "budget_usd": 0.25,
  "budget_seconds": 300,
  "fixtures": {
    "work/input.txt": "the exact bytes this task should find on disk"
  }
}
```

`fixtures` is rewritten before **every** arm, so both arms of a paired trial
start from identical bytes. Declare inputs here rather than placing them by
hand — a benchmark whose setup is manual is one nobody reruns, and one where
arm B silently inherits arm A's edits is worse than no benchmark at all.

Three rules that decide whether the suite is worth anything:

1. **A task the skill was drafted from can never be a trial task.** The gate
   raises `ContaminationError` if you try; do not work around it.
2. **`success_criterion` must be a real verifier**, not `always_pass`. A suite
   scored by `always_pass` measures nothing and will happily promote noise.
3. **Include tasks the skill should NOT help with.** If every task is one the
   skill targets, you are measuring whether the skill applies, not whether it
   helps.

Mark a task catastrophic — a regression on it vetoes promotion outright — by
passing its key in `catastrophic_keys` when you call `evaluate_skill`.
