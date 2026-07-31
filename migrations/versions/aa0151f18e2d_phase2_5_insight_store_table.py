"""phase2.5 insight_store table

Revision ID: aa0151f18e2d
Revises: e8d6f05d71b0
Create Date: 2026-07-31 09:23:05.256869

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'aa0151f18e2d'
down_revision: Union[str, Sequence[str], None] = 'e8d6f05d71b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 2.5 第一批:insight_store 表(完全獨立於 knowledge_state)。

    insight_type / value_text / producer 刻意**不加 DB CHECK** —— insight 標籤還在演化,
    受控放應用層(mes.insight_registry),等穩定再考慮下沉。confidence 沿用 Phase 0 既定
    三級(穩定)→ 加 CHECK。
    """
    op.create_table(
        "insight_store",
        sa.Column("insight_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("insight_type", sa.String(length=50), nullable=False),
        sa.Column("value_text", sa.String(length=255), nullable=False),
        sa.Column("producer", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_knowledge_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.entity_id"]),
        sa.PrimaryKeyConstraint("insight_id"),
        # 同構於 knowledge_state 的 (entity_id, feature):一 entity 的每個 insight 維度
        # 只有一個當前值。第二批全量重算走此鍵 upsert → insight_id 穩定,Phase 3 引用得住。
        sa.UniqueConstraint("entity_id", "insight_type", name="uq_insight_entity_type"),
        sa.CheckConstraint(
            "confidence IN ('certain', 'inferred', 'estimated')", name="ck_insight_confidence"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("insight_store")
