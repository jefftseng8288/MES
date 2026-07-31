"""add review_widget source + review feature vocabulary

Revision ID: 62d66c4849d9
Revises: 23e831093fe6
Create Date: 2026-07-31 13:23:25.658378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62d66c4849d9'
down_revision: Union[str, Sequence[str], None] = '23e831093fe6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """source 加 review_widget:評論數來自 review app 的 widget 頁面(第三方),非店家 html_page。"""
    op.drop_constraint("ck_observation_source", "observation_log", type_="check")
    op.create_check_constraint(
        "ck_observation_source", "observation_log", "source IN ('html_page', 'products_json', 'html_signature', 'web_search', 'review_widget', 'manual', 'monitor')"
    )


def downgrade() -> None:
    """收窄回舊值集 —— 若已有 review_widget 資料會被 DB 擋下(這是正確的保護)。"""
    op.drop_constraint("ck_observation_source", "observation_log", type_="check")
    op.create_check_constraint(
        "ck_observation_source", "observation_log", "source IN ('html_page', 'products_json', 'html_signature', 'web_search', 'manual', 'monitor')"
    )
