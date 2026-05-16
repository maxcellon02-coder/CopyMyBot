"""wizard qualification fields in leads

Revision ID: 003
Revises: 002
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("battery_voltage",   sa.String(20),  nullable=True))
    op.add_column("leads", sa.Column("battery_ah",        sa.String(50),  nullable=True))
    op.add_column("leads", sa.Column("battery_type_pref", sa.String(100), nullable=True))
    op.add_column("leads", sa.Column("size_info",         sa.String(200), nullable=True))
    op.add_column("leads", sa.Column("equipment_type",    sa.String(200), nullable=True))
    op.add_column("leads", sa.Column("quantity_needed",   sa.String(50),  nullable=True))
    op.add_column("leads", sa.Column("company_name",      sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "company_name")
    op.drop_column("leads", "quantity_needed")
    op.drop_column("leads", "equipment_type")
    op.drop_column("leads", "size_info")
    op.drop_column("leads", "battery_type_pref")
    op.drop_column("leads", "battery_ah")
    op.drop_column("leads", "battery_voltage")
