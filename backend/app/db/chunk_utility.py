"""Per-knowledge-chunk utility boosts from analyst feedback.

Closes the loop ``app/knowledge/retriever.py`` already anticipated: ``HybridRetriever.search()`` has taken
a ``boosts`` mapping since step 2, and its own module docstring says the learning service supplies it from
"chunks that have supported confirmed findings" — this is that supplier.
"""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback, FindingRow

_CONFIRM_WEIGHT = 0.2
_DISMISS_WEIGHT = 0.1
_MIN_BOOST = -0.5
_MAX_BOOST = 1.0


async def chunk_boosts(session: AsyncSession) -> dict[str, float]:
    """One entry per knowledge chunk cited by at least one finding with feedback."""
    rows = (
        await session.execute(
            select(Feedback.verdict, FindingRow.chunk_ids).join(
                FindingRow,
                and_(
                    FindingRow.analysis_id == Feedback.analysis_id,
                    FindingRow.finding_id == Feedback.finding_id,
                ),
            )
        )
    ).all()
    counts: dict[str, list[int]] = {}
    for verdict, chunk_ids in rows:
        for chunk_id in chunk_ids:
            bucket = counts.setdefault(chunk_id, [0, 0])
            bucket[0 if verdict == "confirm" else 1] += 1
    return {
        chunk_id: max(_MIN_BOOST, min(_MAX_BOOST, _CONFIRM_WEIGHT * confirms - _DISMISS_WEIGHT * dismisses))
        for chunk_id, (confirms, dismisses) in counts.items()
    }
