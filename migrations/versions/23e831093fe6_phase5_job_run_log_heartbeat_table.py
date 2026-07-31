"""phase5 job_run_log heartbeat table

Revision ID: 23e831093fe6
Revises: 7c7cff956f83
Create Date: 2026-07-31 12:44:24.167864

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '23e831093fe6'
down_revision: Union[str, Sequence[str], None] = '7c7cff956f83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """各鏈路 per-run 心跳:讓警鈴能分辨「沒跑」/「跑了沒產出」/「跑了有產出」。"""
    op.create_table(
        "job_run_log",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("job", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    # 警鈴每次都問「某 job 最近一次心跳」-> 這個索引直接服務該查詢。
    op.create_index("ix_job_run_log_job_finished", "job_run_log", ["job", "finished_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_run_log_job_finished", table_name="job_run_log")
    op.drop_table("job_run_log")
