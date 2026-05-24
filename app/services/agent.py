"""LLM Agent service (placeholder)."""

import logging

logger = logging.getLogger(__name__)


class AgentService:
    """Service for LLM-based Q&A agent."""

    async def query(self, question: str, namespace: str, version: str) -> dict:
        """Process a user question and return an answer with sources."""
        # TODO: implement with LLM + WikiFs + RAG pipeline
        logger.info("Processing query for %s/%s: %s", namespace, version, question[:50])
        return {"answer": "Not implemented yet", "sources": []}