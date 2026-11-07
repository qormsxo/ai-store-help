"""add store and business hours

stores, business_hours 테이블을 만들고 conversations, support_tickets, knowledge_entries에 store_id를 붙인다.

기존 데이터 보존 순서 (PostgreSQL은 DDL도 트랜잭션이라 중간에 실패하면 전체가 롤백된다):
  1. store_id를 nullable로 추가
  2. 세 테이블에 행이 하나라도 있으면 placeholder 매장 1건을 만들어 backfill
  3. NOT NULL 적용 (backfill이 누락됐다면 여기서 실패한다)
  4. 인덱스 교체

행이 없으면 매장을 만들지 않는다. 데모 매장은 seed 스크립트(app/seed.py)의 몫이다.

downgrade 주의: 매장 연결 정보(store_id)는 사라진다. 전역 unique 인덱스를 복원하므로
서로 다른 매장에 같은 질문이 있으면 실패하는데, 단일 매장 정책에서는 해당하지 않는다.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 03:57:08.491509

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import get_settings

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUESTION_KEY = "lower(regexp_replace(btrim(question), '\\s+', ' ', 'g'))"


def upgrade() -> None:
    op.create_table('stores',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('address', sa.String(length=300), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('website', sa.String(length=255), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f('ck_stores_slug_format')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_stores')),
        sa.UniqueConstraint('slug', name=op.f('uq_stores_slug')),
    )
    op.create_table('business_hours',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('store_id', sa.Uuid(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('open_time', sa.Time(), nullable=True),
        sa.Column('close_time', sa.Time(), nullable=True),
        sa.CheckConstraint('(open_time IS NULL) = (close_time IS NULL)', name=op.f('ck_business_hours_open_close_both_or_neither')),
        sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name=op.f('ck_business_hours_day_of_week_range')),
        sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_business_hours_store_id_stores'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_business_hours')),
        sa.UniqueConstraint('store_id', 'day_of_week', name=op.f('uq_business_hours_store_id_day_of_week')),
    )

    # 1) nullable로 먼저 추가한다. NOT NULL로 바로 추가하면 기존 행이 있을 때 실패한다.
    for table in ('conversations', 'support_tickets', 'knowledge_entries'):
        op.add_column(table, sa.Column('store_id', sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f'fk_{table}_store_id_stores'), table, 'stores', ['store_id'], ['id'], ondelete='RESTRICT'
        )

    # 2) 기존 데이터가 있을 때만 placeholder 매장으로 backfill한다.
    connection = op.get_bind()
    has_rows = connection.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM conversations)"
        " OR EXISTS (SELECT 1 FROM support_tickets)"
        " OR EXISTS (SELECT 1 FROM knowledge_entries)"
    )).scalar_one()
    if has_rows:
        params = {'slug': get_settings().default_store_slug, 'name': 'Migrated Store'}
        connection.execute(
            sa.text("INSERT INTO stores (slug, name, timezone) VALUES (:slug, :name, 'UTC')"), params
        )
        connection.execute(
            sa.text("UPDATE conversations SET store_id = (SELECT id FROM stores WHERE slug = :slug)"), params
        )
        connection.execute(sa.text(
            "UPDATE support_tickets t SET store_id = c.store_id FROM conversations c WHERE c.id = t.conversation_id"
        ))
        connection.execute(sa.text(
            "UPDATE knowledge_entries k SET store_id = COALESCE("
            "(SELECT t.store_id FROM support_tickets t WHERE t.id = k.source_ticket_id),"
            " (SELECT id FROM stores WHERE slug = :slug))"
        ), params)

    # 3) NOT NULL 적용
    for table in ('conversations', 'support_tickets', 'knowledge_entries'):
        op.alter_column(table, 'store_id', existing_type=sa.Uuid(), nullable=False)

    # 4) 인덱스 교체: 모두 store_id를 앞에 둔다. 중복 지식 방지도 매장별로 적용된다.
    op.drop_index('ix_conversations_visitor_id_created_at', table_name='conversations')
    op.create_index(
        'ix_conversations_store_id_visitor_id_created_at', 'conversations', ['store_id', 'visitor_id', 'created_at']
    )
    op.drop_index('ix_support_tickets_status_created_at', table_name='support_tickets')
    op.create_index(
        'ix_support_tickets_store_id_status_created_at', 'support_tickets', ['store_id', 'status', 'created_at']
    )
    op.drop_index('uq_knowledge_entries_active_question', table_name='knowledge_entries')
    op.create_index(
        'uq_knowledge_entries_store_active_question',
        'knowledge_entries',
        ['store_id', sa.literal_column(QUESTION_KEY)],
        unique=True,
        postgresql_where=sa.text('is_active'),
    )


def downgrade() -> None:
    op.drop_index('uq_knowledge_entries_store_active_question', table_name='knowledge_entries')
    op.create_index(
        'uq_knowledge_entries_active_question',
        'knowledge_entries',
        [sa.literal_column(QUESTION_KEY)],
        unique=True,
        postgresql_where=sa.text('is_active'),
    )
    op.drop_index('ix_support_tickets_store_id_status_created_at', table_name='support_tickets')
    op.create_index('ix_support_tickets_status_created_at', 'support_tickets', ['status', 'created_at'])
    op.drop_index('ix_conversations_store_id_visitor_id_created_at', table_name='conversations')
    op.create_index('ix_conversations_visitor_id_created_at', 'conversations', ['visitor_id', 'created_at'])

    for table in ('knowledge_entries', 'support_tickets', 'conversations'):
        op.drop_constraint(op.f(f'fk_{table}_store_id_stores'), table, type_='foreignkey')
        op.drop_column(table, 'store_id')

    op.drop_table('business_hours')
    op.drop_table('stores')
