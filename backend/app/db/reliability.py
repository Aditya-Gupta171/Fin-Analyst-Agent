"""Beta-calibrated rule reliability from analyst feedback.

A rule-origin finding reports a flat confidence (``RULE_CONFIDENCE``, app/analysis/baseline.py) until real
feedback exists. This turns confirm/dismiss verdicts into a per-rule score: a Beta posterior mean with a
prior chosen so a rule with zero feedback still scores exactly ``RULE_CONFIDENCE`` — nothing changes until
an analyst has actually weighed in, and then a handful of verdicts noticeably shift it.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.baseline import RULE_CONFIDENCE
from app.db.models import Feedback, FindingRow

# A prior with this much pseudo-weight means ~5 real verdicts are needed to meaningfully move a rule's
# score away from RULE_CONFIDENCE; chosen to be gentle, not to overreact to a single confirm or dismiss.
_PRIOR_STRENGTH = 5
_PRIOR_ALPHA = _PRIOR_STRENGTH * RULE_CONFIDENCE
_PRIOR_BETA = _PRIOR_STRENGTH - _PRIOR_ALPHA


class RuleReliability(BaseModel):
    rule_id: str
    confirms: int
    dismisses: int
    score: float


async def rule_reliabilities(session: AsyncSession) -> dict[str, RuleReliability]:
    """One entry per rule that has at least one piece of feedback, keyed by rule id."""
    rows = (
        await session.execute(
            select(Feedback.verdict, FindingRow.rule_ids).join(
                FindingRow,
                and_(
                    FindingRow.analysis_id == Feedback.analysis_id,
                    FindingRow.finding_id == Feedback.finding_id,
                ),
            )
        )
    ).all()
    counts: dict[str, list[int]] = {}
    for verdict, rule_ids in rows:
        for rule_id in rule_ids:
            bucket = counts.setdefault(rule_id, [0, 0])
            bucket[0 if verdict == "confirm" else 1] += 1
    return {
        rule_id: RuleReliability(
            rule_id=rule_id,
            confirms=confirms,
            dismisses=dismisses,
            score=(_PRIOR_ALPHA + confirms) / (_PRIOR_ALPHA + _PRIOR_BETA + confirms + dismisses),
        )
        for rule_id, (confirms, dismisses) in counts.items()
    }


def scores(reliabilities: dict[str, RuleReliability]) -> dict[str, float]:
    return {rule_id: r.score for rule_id, r in reliabilities.items()}
