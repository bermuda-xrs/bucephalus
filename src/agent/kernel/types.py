from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Taint(str, Enum):
    TRUSTED = "trusted"
    USER = "user"
    UNTRUSTED = "untrusted"

    @property
    def level(self) -> int:
        return {"trusted": 0, "user": 1, "untrusted": 2}[self.value]


def join_taint(*taints: Taint) -> Taint:
    return max(taints, key=lambda t: t.level) if taints else Taint.TRUSTED


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    HALTED = "halted"


class Budget(BaseModel):
    usd: float
    seconds: int
    spent_usd: float = 0.0
    spent_seconds: float = 0.0

    def remaining_usd(self) -> float:
        return max(0.0, self.usd - self.spent_usd)

    def remaining_seconds(self) -> float:
        return max(0.0, self.seconds - self.spent_seconds)

    def exhausted(self) -> bool:
        if self.usd > 0:
            money_gone = self.spent_usd >= self.usd
        else:
            money_gone = self.spent_usd > 0
        return money_gone or self.spent_seconds >= self.seconds


class TaskContract(BaseModel):
    goal: str
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criterion: str = "always_pass"
    artifact_scope: list[str] = Field(default_factory=list)
    grader_dir: str | None = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


class ModelResponse(BaseModel):
    text: str
    usage: TokenUsage
    backend: str
    wall_ms: int
    raw: dict[str, Any] = Field(default_factory=dict)


class VerifyResult(BaseModel):
    passed: bool
    score: float = 0.0
    observation: str = ""
    verifier_id: str = ""


GateVerdict = Literal[
    "allow", "deny", "needs_approval", "promote", "reject", "insufficient"
]


class GateDecision(BaseModel):
    verdict: GateVerdict
    reason: str = ""
    preview: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    @classmethod
    def allow_(cls, reason: str = "") -> GateDecision:
        return cls(verdict="allow", reason=reason)

    @classmethod
    def deny(cls, reason: str) -> GateDecision:
        return cls(verdict="deny", reason=reason)

    @classmethod
    def needs_approval(cls, reason: str, preview: str | None = None) -> GateDecision:
        return cls(verdict="needs_approval", reason=reason, preview=preview)

    @classmethod
    def promote(cls, **detail: Any) -> GateDecision:
        return cls(verdict="promote", reason="gate passed", detail=detail)

    @classmethod
    def reject(cls, reason: str, **detail: Any) -> GateDecision:
        return cls(verdict="reject", reason=reason, detail=detail)

    @classmethod
    def insufficient(cls, reason: str, **detail: Any) -> GateDecision:
        return cls(verdict="insufficient", reason=reason, detail=detail)
