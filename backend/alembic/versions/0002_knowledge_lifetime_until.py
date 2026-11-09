"""rename knowledge lifetime until_end_of_day to until

사장이 고른 날짜까지 지식을 둔다. 오늘 자정으로 고정된 값 이름을 바꾼다.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 10:30:00

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE knowledge_lifetime RENAME VALUE 'until_end_of_day' TO 'until'")


def downgrade() -> None:
    op.execute("ALTER TYPE knowledge_lifetime RENAME VALUE 'until' TO 'until_end_of_day'")
