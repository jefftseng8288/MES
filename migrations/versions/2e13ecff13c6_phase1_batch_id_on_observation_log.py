"""phase1 batch_id on observation_log

Revision ID: 2e13ecff13c6
Revises: d9eb673e28aa
Create Date: 2026-07-14 17:30:34.966355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e13ecff13c6'
down_revision: Union[str, Sequence[str], None] = 'd9eb673e28aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BATCH_ID_CHECK = "batch_id ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2,}$'"

# Backfill existing rows: reconstruct "which run" from the actual observed_at, grouped
# by Taiwan calendar date; within a Taiwan-day, a >10 min gap starts a new batch. Each
# row gets batch_id = <TW date>-NN (NN = cluster order within that day). This keeps the
# already-harvested real observations (Append-Only 精神) and gives every row a valid id.
_BACKFILL_SQL = """
WITH marked AS (
    SELECT observation_id, observed_at,
           (observed_at AT TIME ZONE 'Asia/Taipei')::date AS tw_date,
           CASE
               WHEN lag(observed_at) OVER w IS NULL
                 OR observed_at - lag(observed_at) OVER w > interval '10 minutes'
               THEN 1 ELSE 0
           END AS new_cluster
    FROM observation_log
    WINDOW w AS (
        PARTITION BY (observed_at AT TIME ZONE 'Asia/Taipei')::date
        ORDER BY observed_at, observation_id
    )
),
numbered AS (
    SELECT observation_id, tw_date,
           sum(new_cluster) OVER (
               PARTITION BY tw_date ORDER BY observed_at, observation_id
           ) AS cluster_no
    FROM marked
)
UPDATE observation_log o
SET batch_id = to_char(n.tw_date, 'YYYY-MM-DD') || '-' || lpad(n.cluster_no::text, 2, '0')
FROM numbered n
WHERE o.observation_id = n.observation_id;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("observation_log", sa.Column("batch_id", sa.String(length=16), nullable=True))
    # observation_log has an Append-Only BEFORE UPDATE trigger; the backfill UPDATE would
    # be rejected by it. Disable that trigger only for the backfill, then re-enable.
    op.execute("ALTER TABLE observation_log DISABLE TRIGGER observation_log_no_update")
    op.execute(_BACKFILL_SQL)
    op.execute("ALTER TABLE observation_log ENABLE TRIGGER observation_log_no_update")
    op.alter_column("observation_log", "batch_id", nullable=False)
    op.create_check_constraint("ck_observation_batch_id", "observation_log", _BATCH_ID_CHECK)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_observation_batch_id", "observation_log", type_="check")
    op.drop_column("observation_log", "batch_id")
