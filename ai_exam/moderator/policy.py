"""Trade-off policy: the faculty's priority ranking used by the Moderator to
resolve disagreement between agents in Phase 3.

Phase 0–2 does not consult the policy (no critic disagreement yet), but the
policy is loaded at startup so it is present when Phase 3 lands.
"""

from typing import Literal

from pydantic import BaseModel


PolicyDimension = Literal[
    "content_fidelity",
    "cognitive_alignment",
    "accessibility",
    "discrimination",
    "brevity",
]


class TradeOffPolicy(BaseModel):
    """Resolves disagreement between agents using a priority-ranked tiebreaker.

    priority_rank: ordered list of dimensions, highest priority first. When
    two agents disagree and the disagreement involves dimensions D_a and D_b,
    the dimension that appears earlier in priority_rank wins.

    max_epochs: hard cap on the Phase 3 epoch loop.

    convergence_rule: how the Moderator decides to exit Phase 3 early.
    Currently only one rule is supported.
    """

    priority_rank: list[PolicyDimension]
    max_epochs: int = 4
    convergence_rule: Literal["no_critical_or_high_for_one_epoch"] = (
        "no_critical_or_high_for_one_epoch"
    )

    def winner(self, dim_a: PolicyDimension, dim_b: PolicyDimension) -> PolicyDimension:
        if dim_a not in self.priority_rank or dim_b not in self.priority_rank:
            raise KeyError(
                f"dimension(s) not in priority_rank: {dim_a!r} or {dim_b!r}; "
                f"rank = {self.priority_rank}"
            )
        return dim_a if self.priority_rank.index(dim_a) < self.priority_rank.index(dim_b) else dim_b
