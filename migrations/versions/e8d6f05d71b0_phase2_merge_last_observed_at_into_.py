"""phase2 merge last_observed_at into observed_at (verified identical)

Revision ID: e8d6f05d71b0
Revises: 5c7e8387b736
Create Date: 2026-07-19 09:14:45.203779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8d6f05d71b0'
down_revision: Union[str, Sequence[str], None] = '5c7e8387b736'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPED = ["value_text", "value_number", "value_boolean", "value_json", "value_entity_id"]
_ALL_NULL = " AND ".join(f"{c} IS NULL" for c in _TYPED)
_VT2COL = {"string": "value_text", "number": "value_number", "boolean": "value_boolean",
           "json": "value_json", "entity_ref": "value_entity_id"}
_EXACTLY_ONE = " OR ".join(
    "(" + " AND ".join(
        [f"value_type = '{vt}'", f"{col} IS NOT NULL"]
        + [f"{o} IS NULL" for o in _TYPED if o != col]
    ) + ")"
    for vt, col in _VT2COL.items()
)


def _checks(gate: str) -> dict[str, str]:
    """The 3 gated CHECKs, parameterised by the gating column (last_observed_at | observed_at)."""
    return {
        "ck_knowledge_value_raw": (
            f"({gate} IS NULL AND value_raw IS NULL) "
            f"OR ({gate} IS NOT NULL AND value_raw IS NOT NULL AND btrim(value_raw) <> '')"
        ),
        "ck_knowledge_value_typed": (
            f"({gate} IS NULL AND {_ALL_NULL}) "
            f"OR ({gate} IS NOT NULL AND ({_EXACTLY_ONE}))"
        ),
        "ck_knowledge_status_consistency": f"{gate} IS NOT NULL OR current_status <> 'observed'",
    }


def _drop(names: list[str]) -> None:
    for n in names:
        op.drop_constraint(n, "knowledge_state", type_="check")


def _create(checks: dict[str, str]) -> None:
    for name, expr in checks.items():
        op.create_check_constraint(name, "knowledge_state", expr)


def upgrade() -> None:
    """Merge last_observed_at into observed_at (verified identical: 2905 rows, 0 mismatch).

    knowledge_state is a rebuildable materialized view -> TRUNCATE + re-project after.
    """
    op.execute("TRUNCATE TABLE knowledge_state")
    _drop(list(_checks("last_observed_at").keys()))
    op.drop_column("knowledge_state", "last_observed_at")
    _create(_checks("observed_at"))


def downgrade() -> None:
    op.execute("TRUNCATE TABLE knowledge_state")
    _drop(list(_checks("observed_at").keys()))
    op.add_column(
        "knowledge_state",
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    _create(_checks("last_observed_at"))
