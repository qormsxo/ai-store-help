"""initial schema

현재 모델과 같은 스키마를 한 번에 만든다.
매장, 영업시간, 대화, 메시지, 문의 티켓, 지식.

Revision ID: 0001
Revises:
Create Date: 2026-11-08 19:50:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUESTION_KEY = "lower(regexp_replace(btrim(question), '\\s+', ' ', 'g'))"


def upgrade() -> None:
    op.create_table(
        "stores",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_stores_slug_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("slug", name=op.f("uq_stores_slug")),
    )
    op.create_table(
        "business_hours",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=True),
        sa.Column("close_time", sa.Time(), nullable=True),
        sa.CheckConstraint(
            "(open_time IS NULL) = (close_time IS NULL)", name=op.f("ck_business_hours_open_close_both_or_neither")
        ),
        sa.CheckConstraint("day_of_week BETWEEN 0 AND 6", name=op.f("ck_business_hours_day_of_week_range")),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_business_hours_store_id_stores"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_hours")),
        sa.UniqueConstraint("store_id", "day_of_week", name=op.f("uq_business_hours_store_id_day_of_week")),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_conversations_store_id_stores"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_store_id_visitor_id_created_at",
        "conversations",
        ["store_id", "visitor_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", "system", name="message_role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at", "messages", ["conversation_id", "created_at"], unique=False
    )
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "escalation_reason",
            sa.Enum("no_knowledge", "insufficient_knowledge", name="escalation_reason"),
            nullable=False,
        ),
        sa.Column("owner_answer", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", name="ticket_status"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status = 'open' OR (resolved_at IS NOT NULL AND owner_answer IS NOT NULL)",
            name=op.f("ck_support_tickets_resolved_requires_answer"),
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_support_tickets_store_id_stores"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_support_tickets_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name=op.f("fk_support_tickets_source_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_tickets")),
    )
    op.create_index("ix_support_tickets_conversation_id", "support_tickets", ["conversation_id"], unique=False)
    op.create_index(
        "ix_support_tickets_store_id_status_created_at",
        "support_tickets",
        ["store_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_support_tickets_source_message_id", "support_tickets", ["source_message_id"], unique=True
    )
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.String(length=500), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_knowledge_entries_store_id_stores"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_ticket_id"],
            ["support_tickets.id"],
            name=op.f("fk_knowledge_entries_source_ticket_id_support_tickets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_entries")),
        sa.UniqueConstraint("source_ticket_id", name=op.f("uq_knowledge_entries_source_ticket_id")),
    )
    op.create_index(
        "uq_knowledge_entries_store_active_question",
        "knowledge_entries",
        ["store_id", sa.literal_column(QUESTION_KEY)],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_knowledge_entries_store_active_question",
        table_name="knowledge_entries",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("knowledge_entries")
    op.drop_index("uq_support_tickets_source_message_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_store_id_status_created_at", table_name="support_tickets")
    op.drop_index("ix_support_tickets_conversation_id", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_store_id_visitor_id_created_at", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("business_hours")
    op.drop_table("stores")
    # drop_table은 PostgreSQL enum 타입을 지우지 않는다. 남기면 다시 upgrade할 때 "type already exists"로 실패한다.
    for enum_name in ("ticket_status", "escalation_reason", "message_role"):
        op.execute(f"DROP TYPE {enum_name}")
