"""phase1d store_harvest_state + mes_store_crawler_v1 producer

Revision ID: 91b83d1ec4a3
Revises: 2e13ecff13c6
Create Date: 2026-07-15 21:51:48.798494

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91b83d1ec4a3'
down_revision: Union[str, Sequence[str], None] = '2e13ecff13c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRODUCER_OLD = "producer IN ('mes_crawler_v1', 'duckduckgo_v1', 'manual_v1')"
_PRODUCER_NEW = (
    "producer IN ('mes_crawler_v1', 'duckduckgo_v1', 'manual_v1', 'mes_store_crawler_v1')"
)


def upgrade() -> None:
    """Upgrade schema."""
    # store_harvest_state: system processing state (mutable; NOT an observation).
    op.create_table(
        "store_harvest_state",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_harvest_status"),
        sa.ForeignKeyConstraint(["entity_id"], ["entity.entity_id"]),
        sa.PrimaryKeyConstraint("entity_id"),
    )
    # producer CHECK += 'mes_store_crawler_v1' on both tables.
    for table, ck in (("observation_log", "ck_observation_producer"),
                      ("knowledge_state", "ck_knowledge_producer")):
        op.drop_constraint(ck, table, type_="check")
        op.create_check_constraint(ck, table, _PRODUCER_NEW)


def downgrade() -> None:
    """Downgrade schema."""
    for table, ck in (("observation_log", "ck_observation_producer"),
                      ("knowledge_state", "ck_knowledge_producer")):
        op.drop_constraint(ck, table, type_="check")
        op.create_check_constraint(ck, table, _PRODUCER_OLD)
    op.drop_table("store_harvest_state")
