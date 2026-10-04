"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class QARun(Base):
    """Persisted QA run record."""

    __tablename__ = "qa_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    username: Mapped[str | None] = mapped_column(String(512), nullable=True)
    password_masked: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    current_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    pages_visited: Mapped[int] = mapped_column(Integer, default=0)
    actions_taken: Mapped[int] = mapped_column(Integer, default=0)
    bugs_found: Mapped[int] = mapped_column(Integer, default=0)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PageRecord(Base):
    """Discovered/explored page within a run (one row per run_id + canonical URL)."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("run_id", "url", name="uq_pages_run_canonical_url"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    url: Mapped[str] = mapped_column(String(2048))  # canonical URL
    title: Mapped[str] = mapped_column(String(1024), default="")
    page_type: Mapped[str] = mapped_column(String(128), default="unknown")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    visit_count: Mapped[int] = mapped_column(Integer, default=1)
    exploration_status: Mapped[str] = mapped_column(String(32), default="discovered")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActionRecord(Base):
    """Executed browser action log."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BugRecord(Base):
    """Detected defect linked to a run."""

    __tablename__ = "bugs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(32), default="major")
    status: Mapped[str] = mapped_column(String(32), default="open")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EvidenceRecord(Base):
    """Evidence artifact metadata."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="screenshot")
    path: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str] = mapped_column(Text, default="")
    page_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventRecord(Base):
    """Persisted run event for history / WS replay."""

    __tablename__ = "run_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatConversation(Base):
    """A chat conversation session with the QA agent."""
    
    __tablename__ = "chat_conversations"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active, archived, deleted
    target_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    context_json: Mapped[str] = mapped_column(Text, default="{}")  # Stores conversation memory
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    """Individual message in a chat conversation."""
    
    __tablename__ = "chat_messages"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(32))  # user, assistant, system
    content: Mapped[str] = mapped_column(Text)
    intent_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Parsed user instruction
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # Associated QA run
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationContext:
    """Manages conversation context and memory across messages."""
    
    def __init__(self):
        self.target_url: str | None = None
        self.tested_modules: list[str] = []
        self.discovered_features: dict[str, list[str]] = {}
        self.previous_findings: list[dict] = []
        self.user_preferences: dict = {}
        self.active_run_id: str | None = None
        
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
