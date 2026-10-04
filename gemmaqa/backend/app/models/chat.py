"""Chat models for conversational QA interface."""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.models.base import Base


class ChatConversation(Base):
    """A chat conversation session with the QA agent."""
    
    __tablename__ = "chat_conversations"
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=True)  # Auto-generated from first message
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Conversation state
    status = Column(String, default="active")  # active, archived, deleted
    target_url = Column(String, nullable=True)  # Current target URL
    
    # Accumulated context (JSON)
    context = Column(JSON, default=dict)  # Stores conversation memory
    
    # Relationships
    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")
    qa_runs = relationship("QARun", back_populates="chat_conversation")


class ChatMessage(Base):
    """Individual message in a chat conversation."""
    
    __tablename__ = "chat_messages"
    
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("chat_conversations.id"))
    
    # Message content
    role = Column(String)  # user, assistant, system
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Parsed intent (JSON)
    intent = Column(JSON, nullable=True)  # Parsed user instruction
    
    # Associated QA run (if this message triggered a test)
    run_id = Column(String, ForeignKey("qa_runs.id"), nullable=True)
    
    # Relationships
    conversation = relationship("ChatConversation", back_populates="messages")
    run = relationship("QARun", foreign_keys=[run_id])


class ConversationContext:
    """Manages conversation context and memory across messages."""
    
    def __init__(self):
        self.target_url: Optional[str] = None
        self.tested_modules: list[str] = []
        self.discovered_features: dict[str, list[str]] = {}
        self.previous_findings: list[dict] = []
        self.user_preferences: dict = {}
        self.active_run_id: Optional[str] = None
        
    def to_dict(self) -> dict:
        return {
            "target_url": self.target_url,
            "tested_modules": self.tested_modules,
            "discovered_features": self.discovered_features,
            "previous_findings": self.previous_findings,
            "user_preferences": self.user_preferences,
            "active_run_id": self.active_run_id,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ConversationContext":
        ctx = cls()
        ctx.target_url = data.get("target_url")
        ctx.tested_modules = data.get("tested_modules", [])
        ctx.discovered_features = data.get("discovered_features", {})
        ctx.previous_findings = data.get("previous_findings", [])
        ctx.user_preferences = data.get("user_preferences", {})
        ctx.active_run_id = data.get("active_run_id")
        return ctx
    
    def update_from_run(self, run_result: dict):
        """Update context based on QA run results."""
        if "modules_tested" in run_result:
            self.tested_modules.extend(run_result["modules_tested"])
        if "features_discovered" in run_result:
            for module, features in run_result["features_discovered"].items():
                if module not in self.discovered_features:
                    self.discovered_features[module] = []
                self.discovered_features[module].extend(features)
        if "findings" in run_result:
            self.previous_findings.append(run_result["findings"])
