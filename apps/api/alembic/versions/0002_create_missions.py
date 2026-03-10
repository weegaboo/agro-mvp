"""create missions table"""

from alembic import op
import sqlalchemy as sa


revision = "0002_create_missions"
down_revision = "0001_create_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_missions_id", "missions", ["id"])
    op.create_index("ix_missions_user_id", "missions", ["user_id"])
    op.create_index("ix_missions_status", "missions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_missions_status", table_name="missions")
    op.drop_index("ix_missions_user_id", table_name="missions")
    op.drop_index("ix_missions_id", table_name="missions")
    op.drop_table("missions")
