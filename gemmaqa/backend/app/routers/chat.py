"""Chat API endpoints for conversational QA interface."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.chat.orchestrator import ChatOrchestrator
from app.agent.run_manager import RunManager
from app.models import ChatConversation, ChatMessage


router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class ChatSettingsPayload(BaseModel):
    targetUrl: Optional[str] = None
    testingObjective: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    allowControlledWrites: bool = False
    allowTestDataCreation: bool = False
    allowDeletion: bool = False
    headlessMode: bool = True
    safeMode: bool = True
    maxActions: Optional[str] = None
    # Test case types to execute: "positive" (happy path) and/or "negative" (error cases)
    testCaseTypes: list[str] = ["positive"]


class SendMessageRequest(BaseModel):
    message: str
    settings: Optional[ChatSettingsPayload] = None


@router.post("/conversations")
async def create_conversation(
    request: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat conversation."""
    orchestrator = ChatOrchestrator(db, RunManager())
    conversation = await orchestrator.create_conversation(request.title)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get conversation details with message history."""
    from sqlalchemy import select
    import json
    
    # Get conversation
    result = await db.execute(
        select(ChatConversation)
        .where(ChatConversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Get messages separately
    messages_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.timestamp)
    )
    messages = messages_result.scalars().all()
    
    # Parse context JSON
    context_data = json.loads(conversation.context_json) if conversation.context_json != '{}' else {}
    
    return {
        "id": conversation.id,
        "title": conversation.title,
        "status": conversation.status,
        "target_url": conversation.target_url,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
        "context": context_data,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "run_id": msg.run_id,
            }
            for msg in messages
        ],
    }


@router.get("/conversations")
async def list_conversations(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List all conversations."""
    from sqlalchemy import select, desc
    
    result = await db.execute(
        select(ChatConversation)
        .where(ChatConversation.status == "active")
        .order_by(desc(ChatConversation.updated_at))
        .limit(limit)
    )
    conversations = result.scalars().all()
    
    return {
        "conversations": [
            {
                "id": conv.id,
                "title": conv.title,
                "target_url": conv.target_url,
                "updated_at": conv.updated_at.isoformat(),
            }
            for conv in conversations
        ]
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message and stream the response.
    
    Returns Server-Sent Events (SSE) stream with JSON chunks.
    """
    orchestrator = ChatOrchestrator(db, RunManager())
    
    async def event_generator():
        """Generate SSE events."""
        async for chunk in orchestrator.send_message(
            conversation_id, request.message, request.settings
        ):
            yield f"data: {chunk}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation."""
    from sqlalchemy import select
    
    result = await db.execute(
        select(ChatConversation).where(ChatConversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    conversation.status = "deleted"
    await db.commit()
    
    return {"message": "Conversation deleted"}
