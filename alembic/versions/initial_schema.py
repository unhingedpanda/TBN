"""initial_schema

Revision ID: 001
Revises: 
Create Date: 2024-10-14 16:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create cases table
    op.create_table('cases',
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('customer_identifier', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.Column('message_count', sa.Integer(), nullable=True),
        sa.Column('escalated', sa.Boolean(), nullable=True),
        sa.Column('escalated_at', sa.DateTime(), nullable=True),
        sa.Column('last_escalation_alert', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('case_id')
    )
    op.create_index(op.f('ix_cases_case_id'), 'cases', ['case_id'], unique=False)
    op.create_index(op.f('ix_cases_customer_identifier'), 'cases', ['customer_identifier'], unique=False)

    # Create messages table
    op.create_table('messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('case_id', sa.String(), nullable=False),
        sa.Column('sender', sa.String(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['cases.case_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_id'), 'messages', ['id'], unique=False)

    # Create processed_messages table for deduplication
    op.create_table('processed_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('case_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['cases.case_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'source', name='uq_processed_message_id_source')
    )
    op.create_index(op.f('ix_processed_messages_id'), 'processed_messages', ['id'], unique=False)
    op.create_index(op.f('ix_processed_messages_message_id'), 'processed_messages', ['message_id'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_constraint('uq_processed_message_id_source', 'processed_messages', type_='unique')
    op.drop_index(op.f('ix_processed_messages_message_id'), table_name='processed_messages')
    op.drop_index(op.f('ix_processed_messages_id'), table_name='processed_messages')
    op.drop_table('processed_messages')
    op.drop_index(op.f('ix_messages_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_cases_customer_identifier'), table_name='cases')
    op.drop_index(op.f('ix_cases_case_id'), table_name='cases')
    op.drop_table('cases')
