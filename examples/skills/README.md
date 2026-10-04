# Example skills

Hand-written skills, kept as examples of the shape. Nothing here is live:
loading one makes it a **candidate**, and only the M9 gate promotes, only
after 30 paired trials clear all four conditions, and only with a human
approving.

```bat
.\alpha.ps1 skills --add examples\skills\verbatim.md --name verbatim --when "asked what a file says"
python bench\run.py paired <skill_id> --suite bench\tasks
.\alpha.ps1 skills --gate <skill_id>
```

The shape that matters, from M9.3 and the evidence behind it:

- **Three modules or fewer.** `SkillStore.store` refuses more. Focused skills
  helped in the literature; "comprehensive" ones measurably hurt.
- **Imperative and checkable.** "Return the contents verbatim" can be tested;
  "be thorough and accurate" cannot, and a skill made of that is the filler
  the gate is built to reject.
- **State when it applies** in `--when`. Retrieval is measured separately from
  quality (M9.4), so a good skill with a vague trigger fails for a reason that
  has nothing to do with the skill.

Expect most candidates to be rejected. Published evidence is that
self-generated skills average slightly negative; a 10-30% promotion rate is
the system working, and above 60% means checking for contamination first.
