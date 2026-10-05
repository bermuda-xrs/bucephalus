from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..kernel.events import canonical, sha256_hex
from ..kernel.types import Taint

ActionMode = Literal["code", "direct", "delegate"]


class PlanStep(BaseModel):
    id: str
    intent: str
    action_mode: ActionMode = "direct"
    tools: list[str] = Field(default_factory=list)
    artifact_scope: list[str] = Field(default_factory=list)
    max_taint_out: Taint = Taint.USER
    est_tokens: int = 0
    est_seconds: float = 0.0
    est_cost_usd: float = 0.0
    success_criterion: str = "always_pass"
    reversible: bool = True
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    id: str = ""
    objective: str
    steps: list[PlanStep] = Field(default_factory=list)
    total_est_cost_usd: float = 0.0
    total_est_seconds: float = 0.0
    human_approvals_expected: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    def body(self) -> dict:
        d = self.model_dump(mode="json")
        d.pop("id", None)
        return d

    def content_hash(self) -> str:
        return sha256_hex(canonical(self.body()))

    def seal(self) -> "Plan":
        self.total_est_cost_usd = round(sum(s.est_cost_usd for s in self.steps), 6)
        self.total_est_seconds = round(sum(s.est_seconds for s in self.steps), 3)
        self.id = self.content_hash()
        return self

    def step(self, step_id: str) -> PlanStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def render(self) -> str:
        lines = [f"objective: {self.objective}", ""]
        for s in self.steps:
            flag = "" if s.reversible else "  [IRREVERSIBLE]"
            lines.append(
                f"  {s.id}. {s.intent}{flag}\n"
                f"      mode={s.action_mode} tools={','.join(s.tools) or '-'} "
                f"scope={','.join(s.artifact_scope) or '-'}\n"
                f"      check={s.success_criterion} "
                f"est=${s.est_cost_usd:.4f} / {s.est_seconds:.0f}s / {s.est_tokens} tok"
            )
        lines.append("")
        lines.append(
            f"total: ${self.total_est_cost_usd:.4f}, {self.total_est_seconds:.0f}s, "
            f"{len(self.steps)} steps"
        )
        if self.human_approvals_expected:
            lines.append(
                "will PARK for you at: " + ", ".join(self.human_approvals_expected)
            )
        else:
            lines.append("will PARK for you at: nothing (runs unattended end to end)")
        if self.open_questions:
            lines.append("open questions:")
            lines += [f"  ? {q}" for q in self.open_questions]
        if self.unknowns:
            lines.append("unknowns:")
            lines += [f"  ~ {u}" for u in self.unknowns]
        return "\n".join(lines)
