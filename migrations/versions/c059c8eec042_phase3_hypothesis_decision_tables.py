"""phase3 hypothesis + decision tables

Revision ID: c059c8eec042
Revises: 62d66c4849d9
Create Date: 2026-08-03 12:04:00.434109

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c059c8eec042'
down_revision: Union[str, Sequence[str], None] = '62d66c4849d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 3 第一批:hypothesis(會死的預測)+ decision(Decision Graph)。

    predicted_outcome 刻意**無** DB CHECK(合法值取決於未定的 Phase 4 武器,受控放應用層);
    status / confidence / action 已定義完整且穩定 -> 有 DB CHECK。同表刻意兩種待遇。
    """
    op.create_table(
        "hypothesis",
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("pattern", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("predicted_outcome", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("source_insight_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("hypothesis_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("parent_hypothesis_id", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("hypothesis_id"),
        sa.ForeignKeyConstraint(["parent_hypothesis_id"], ["hypothesis.hypothesis_id"]),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'retired')",
            name="ck_hypothesis_status",
        ),
        sa.CheckConstraint(
            "confidence IN ('certain', 'inferred', 'estimated')", name="ck_hypothesis_confidence"
        ),
        # Provenance 鐵律:上游引用不可為空(NOT NULL 擋不住空陣列)。
        sa.CheckConstraint(
            "jsonb_typeof(source_insight_refs) = 'array' "
            "AND jsonb_array_length(source_insight_refs) > 0",
            name="ck_hypothesis_source_refs_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(pattern) = 'array' AND jsonb_array_length(pattern) > 0",
            name="ck_hypothesis_pattern_nonempty",
        ),
    )
    # 審核流程會查「待審的假說」;演化鏈查詢會走 parent。
    op.create_index("ix_hypothesis_status", "hypothesis", ["status"])

    op.create_table(
        "decision",
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("parent_decision_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.ForeignKeyConstraint(["parent_decision_id"], ["decision.decision_id"]),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'comment')", name="ck_decision_action"
        ),
    )
    # 「某個對象的決策史」是主要查詢(泛型指向,故無 FK 可依賴)。
    op.create_index("ix_decision_target", "decision", ["target_type", "target_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_decision_target", table_name="decision")
    op.drop_table("decision")
    op.drop_index("ix_hypothesis_status", table_name="hypothesis")
    op.drop_table("hypothesis")
