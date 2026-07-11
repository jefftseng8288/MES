"""phase1c producer web_search source crawler_version

Revision ID: d9eb673e28aa
Revises: f215450ec0a6
Create Date: 2026-07-11 18:46:22.190275

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9eb673e28aa'
down_revision: Union[str, Sequence[str], None] = 'f215450ec0a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE_OLD = "source IN ('html_page', 'products_json', 'html_signature', 'manual', 'monitor')"
_SOURCE_NEW = (
    "source IN ('html_page', 'products_json', 'html_signature', "
    "'web_search', 'manual', 'monitor')"
)
_PRODUCER_CHECK = "producer IN ('mes_crawler_v1', 'duckduckgo_v1', 'manual_v1')"


def _add_producer(table: str, ck_name: str) -> None:
    """Add a NOT NULL producer column + CHECK. Backfills existing rows to keep the
    NOT NULL add safe on a populated table (migration-safety value only)."""
    op.add_column(table, sa.Column("producer", sa.String(length=32), nullable=True))
    op.execute(f"UPDATE {table} SET producer = 'mes_crawler_v1' WHERE producer IS NULL")
    op.alter_column(table, "producer", nullable=False)
    op.create_check_constraint(ck_name, table, _PRODUCER_CHECK)


def upgrade() -> None:
    """Upgrade schema."""
    # 1) source: add 'web_search' to the controlled CHECK (physically, not just a comment).
    op.drop_constraint("ck_observation_source", "observation_log", type_="check")
    op.create_check_constraint("ck_observation_source", "observation_log", _SOURCE_NEW)

    # 2) producer column on both tables (NOT NULL + CHECK).
    _add_producer("observation_log", "ck_observation_producer")
    _add_producer("knowledge_state", "ck_knowledge_producer")

    # 3) crawler_version 歸位: remove the producer tag that was stashed there in 1-C.
    #    crawler_version now holds only a git SHA-1 (or NULL until wired).
    op.execute("UPDATE observation_log SET crawler_version = NULL WHERE crawler_version = 'duckduckgo_v1'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_knowledge_producer", "knowledge_state", type_="check")
    op.drop_column("knowledge_state", "producer")
    op.drop_constraint("ck_observation_producer", "observation_log", type_="check")
    op.drop_column("observation_log", "producer")

    op.drop_constraint("ck_observation_source", "observation_log", type_="check")
    op.create_check_constraint("ck_observation_source", "observation_log", _SOURCE_OLD)
