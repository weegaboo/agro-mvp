"""create users table"""

from alembic import op
import sqlalchemy as sa


revision = '0001_create_users'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('login', sa.String(length=100), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
    )
    op.create_index('ix_users_id', 'users', ['id'])
    op.create_index('ix_users_login', 'users', ['login'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_login', table_name='users')
    op.drop_index('ix_users_id', table_name='users')
    op.drop_table('users')
