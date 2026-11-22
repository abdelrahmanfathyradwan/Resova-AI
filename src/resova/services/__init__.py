"""Application services orchestrating sources, scoring and notifications (Phase 4-6).

Services wire together the sources, AI analysis, database, and Telegram notification
layers. Each service has a focused responsibility and clear boundaries.
"""

from resova.services.ingestion import IngestionService
from resova.services.proposal import ProposalService

__all__ = ["IngestionService", "ProposalService"]
