"""Database models."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationState(Base):
    """Singleton-style durable flags used for security-sensitive setup state."""

    __tablename__ = "application_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    boolean_value: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """A local Tianxu account with one of the two supported roles."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user", index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", index=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuthSession(Base):
    """A revocable browser session; only the SHA-256 token digest is stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentConversation(Base):
    """One user-owned multi-turn Agent conversation."""

    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index("ix_agent_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(100), default="新对话", server_default="新对话")
    birth_input: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentConversationMessage(Base):
    """A normalized message with an optional compact administrator trace."""

    __tablename__ = "agent_conversation_messages"
    __table_args__ = (
        Index("ix_agent_conversation_messages_conversation_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    agent_trace: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        deferred=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginThrottle(Base):
    """Database-backed login throttling that works across application instances."""

    __tablename__ = "login_throttles"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    """Security-relevant administrator actions without sensitive payloads."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class KnowledgeDocument(Base):
    """One administrator-uploaded TXT file stored exactly as received."""

    __tablename__ = "knowledge_documents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    encoding: Mapped[str] = mapped_column(String(32))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class GraphOrganizingJob(Base):
    """One automatic TXT-to-Neo4j organizing run."""

    __tablename__ = "graph_organizing_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    document_title: Mapped[str] = mapped_column(String(200))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    api_protocol: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    prompt_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default="queued", index=True
    )
    total_sections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processed_sections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    current_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rules_extracted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rules_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rules_merged: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    conditions_written: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    relations_written: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    conflicts_written: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ignored_sections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GraphOrganizingTrace(Base):
    """One completed model attempt for one temporary document section."""

    __tablename__ = "graph_organizing_traces"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "section_index",
            "attempt",
            name="uq_graph_organizing_traces_job_section_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("graph_organizing_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    section_index: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    rules_extracted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    agent_trace: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCredential(Base):
    """One encrypted platform model credential managed by administrators."""

    __tablename__ = "model_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    api_protocol: Mapped[str] = mapped_column(
        String(32), default="responses", server_default="responses"
    )
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_key_last_four: Mapped[str] = mapped_column(String(4))
    encryption_key_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvaluationRun(Base):
    """A durable administrator-triggered benchmark run."""

    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_name: Mapped[str] = mapped_column(String(64))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    dataset_question_count: Mapped[int] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String(16))
    benchmark_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="tianxu_fortune")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    provider: Mapped[str] = mapped_column(String(32))
    api_protocol: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    prompt_version: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(64))
    calculation_policy_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default="queued", index=True
    )
    total_questions: Mapped[int] = mapped_column(Integer)
    completed_questions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    correct_answers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvaluationItem(Base):
    """One scored MingLi question inside an evaluation run."""

    __tablename__ = "evaluation_items"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_evaluation_items_run_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(32), index=True)
    case_id: Mapped[str] = mapped_column(String(32), index=True)
    benchmark_year: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    correct_answer: Mapped[str] = mapped_column(String(1))
    predicted_answer: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", index=True
    )
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_trace: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
