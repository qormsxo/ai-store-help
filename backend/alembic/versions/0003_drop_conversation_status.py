"""drop conversation status

대화를 닫는 기능이 없어서 status는 항상 active였다. 컬럼과 enum을 지운다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 12:20:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("conversations", "status")
    # drop_column은 PostgreSQL enum 타입을 지우지 않는다.
    op.execute("DROP TYPE conversation_status")


def downgrade() -> None:
    status = sa.Enum("active", "closed", name="conversation_status", create_type=False)
    status.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "conversations",
        sa.Column("status", status, server_default="active", nullable=False),
    )
