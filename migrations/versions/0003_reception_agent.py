"""Durable intent processing and factual assistant replies.

Revision ID: 0003_agent
Revises: 0002_reception
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_agent"
down_revision = "0002_reception"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_reception_messages_message_status"), "reception_messages")
    op.create_check_constraint(
        op.f("ck_reception_messages_message_status"),
        "reception_messages",
        "status IN ('queued', 'processing', 'needs_order', 'ready_for_agent', "
        "'completed', 'failed', 'superseded')",
    )
    op.add_column("reception_messages", sa.Column("intent", JSONB(), nullable=True))
    op.add_column(
        "reception_messages",
        sa.Column("context", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("reception_messages", sa.Column("worker_token", sa.Uuid(), nullable=True))
    op.create_table(
        "reception_replies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("facts", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_reception_replies"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"], ["reception_messages.tenant_id", "reception_messages.id"]
        ),
        sa.UniqueConstraint("tenant_id", "message_id", "phase"),
        sa.CheckConstraint("phase IN ('clarification', 'answer', 'error')", name="reply_phase"),
        sa.CheckConstraint("length(btrim(text)) BETWEEN 1 AND 12000", name="reply_length"),
    )


def downgrade():
    # Refuse destructive downgrade while there is work using the new state machine.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM reception_messages "
            "WHERE status IN ('queued', 'processing', 'completed', 'failed'))"
        )
    ):
        raise RuntimeError("Archive Agent messages before downgrading")
    op.drop_table("reception_replies")
    for column in ("worker_token", "context", "intent"):
        op.drop_column("reception_messages", column)
    op.drop_constraint(op.f("ck_reception_messages_message_status"), "reception_messages")
    op.create_check_constraint(
        op.f("ck_reception_messages_message_status"),
        "reception_messages",
        "status IN ('needs_order', 'ready_for_agent', 'superseded')",
    )
