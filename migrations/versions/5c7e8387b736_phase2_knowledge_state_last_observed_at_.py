"""phase2 knowledge_state last_observed_at + current_status + gated CHECK

Revision ID: 5c7e8387b736
Revises: 517925c07507
Create Date: 2026-07-19 08:13:59.501683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c7e8387b736'
down_revision: Union[str, Sequence[str], None] = '517925c07507'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPED = ["value_text", "value_number", "value_boolean", "value_json", "value_entity_id"]
_ALL_TYPED_NULL = " AND ".join(f"{c} IS NULL" for c in _TYPED)
_VT2COL = {"string": "value_text", "number": "value_number", "boolean": "value_boolean",
           "json": "value_json", "entity_ref": "value_entity_id"}
_EXACTLY_ONE = " OR ".join(
    "(" + " AND ".join(
        [f"value_type = '{vt}'", f"{col} IS NOT NULL"]
        + [f"{o} IS NULL" for o in _TYPED if o != col]
    ) + ")"
    for vt, col in _VT2COL.items()
)

# old (unconditional) — must be dropped: they conflict with the new "no value" state.
_OLD_VALUE_RAW = "value_raw IS NOT NULL AND btrim(value_raw) <> ''"
_OLD_TYPED = _EXACTLY_ONE

# new (Phase 2): value presence gated by last_observed_at.
_NEW_VALUE_RAW = (
    "(last_observed_at IS NULL AND value_raw IS NULL) "
    "OR (last_observed_at IS NOT NULL AND value_raw IS NOT NULL AND btrim(value_raw) <> '')"
)
_NEW_TYPED = (
    f"(last_observed_at IS NULL AND {_ALL_TYPED_NULL}) "
    f"OR (last_observed_at IS NOT NULL AND ({_EXACTLY_ONE}))"
)
_CURRENT_STATUS = "current_status IN ('observed', 'fetch_failed', 'not_found')"
_STATUS_CONSISTENCY = "last_observed_at IS NOT NULL OR current_status <> 'observed'"


def upgrade() -> None:
    """Upgrade schema."""
    # knowledge_state is a materialized view (rebuildable from observation_log); clear any
    # existing rows so the new columns/CHECKs apply to a clean slate (Phase 2 projection not
    # built yet — nothing worth keeping).
    op.execute("TRUNCATE TABLE knowledge_state")

    op.add_column(
        "knowledge_state", sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_state", sa.Column("current_status", sa.String(length=16), nullable=False)
    )

    # replace the unconditional value CHECKs with last_observed_at-gated ones
    op.drop_constraint("ck_knowledge_value_raw", "knowledge_state", type_="check")
    op.drop_constraint("ck_knowledge_value_typed", "knowledge_state", type_="check")
    op.create_check_constraint("ck_knowledge_value_raw", "knowledge_state", _NEW_VALUE_RAW)
    op.create_check_constraint("ck_knowledge_value_typed", "knowledge_state", _NEW_TYPED)
    op.create_check_constraint("ck_knowledge_current_status", "knowledge_state", _CURRENT_STATUS)
    op.create_check_constraint(
        "ck_knowledge_status_consistency", "knowledge_state", _STATUS_CONSISTENCY
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("TRUNCATE TABLE knowledge_state")
    op.drop_constraint("ck_knowledge_status_consistency", "knowledge_state", type_="check")
    op.drop_constraint("ck_knowledge_current_status", "knowledge_state", type_="check")
    op.drop_constraint("ck_knowledge_value_typed", "knowledge_state", type_="check")
    op.drop_constraint("ck_knowledge_value_raw", "knowledge_state", type_="check")
    op.create_check_constraint("ck_knowledge_value_raw", "knowledge_state", _OLD_VALUE_RAW)
    op.create_check_constraint("ck_knowledge_value_typed", "knowledge_state", _OLD_TYPED)
    op.drop_column("knowledge_state", "current_status")
    op.drop_column("knowledge_state", "last_observed_at")
