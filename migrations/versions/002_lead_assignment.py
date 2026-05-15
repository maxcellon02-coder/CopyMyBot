"""lead assignment fields

Revision ID: 002
Revises: 001
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("manager_message_id", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("assigned_to", sa.String(200), nullable=True))
    op.add_column("leads", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "assigned_at")
    op.drop_column("leads", "assigned_to")
    op.drop_column("leads", "manager_message_id")
