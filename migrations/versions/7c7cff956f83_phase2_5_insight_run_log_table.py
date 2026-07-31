"""phase2.5 insight_run_log table

Revision ID: 7c7cff956f83
Revises: aa0151f18e2d
Create Date: 2026-07-31 10:17:29.250523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c7cff956f83'
down_revision: Union[str, Sequence[str], None] = 'aa0151f18e2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 2.5 第二批:insight_run_log —— 記錄「某 entity 的某 insight_type 為什麼沒產出」。

    不塞進 insight_store(value_text NOT NULL;塞「資料不足」會把系統計算狀態混進市場描述)。
    獨立記錄,比照 alert_log 精神:結構化、可查詢、可聚合。
    """
    op.create_table(
        "insight_run_log",
        sa.Column("run_log_id", sa.Uuid(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("insight_type", sa.String(length=50), nullable=False),
        sa.Column("producer", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.entity_id"]),
        sa.PrimaryKeyConstraint("run_log_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("insight_run_log")
