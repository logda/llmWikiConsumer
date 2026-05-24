"""Chat API endpoints - user Q&A with wiki knowledge base."""

from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/query", response_model=ChatResponse)
async def query_wiki(request: ChatRequest) -> ChatResponse:
    """Query the wiki knowledge base with a natural language question."""
    # TODO: implement agent-based Q&A
    return ChatResponse(answer="Not implemented yet", sources=[])
