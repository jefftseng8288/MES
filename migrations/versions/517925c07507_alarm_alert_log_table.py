"""alarm alert_log table

Revision ID: 517925c07507
Revises: 91b83d1ec4a3
Create Date: 2026-07-17 16:31:46.873256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '517925c07507'
down_revision: Union[str, Sequence[str], None] = '91b83d1ec4a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alert_log",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taiwan_date", sa.String(length=10), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),  # not CHECK-locked
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("alert_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("alert_log")
