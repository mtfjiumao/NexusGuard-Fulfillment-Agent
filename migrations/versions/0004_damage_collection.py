"""Persistent multi-item damage collection and real uploaded image bytes.

Revision ID: 0004_damage
Revises: 0003_agent
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_damage"
down_revision = "0003_agent"
branch_labels = None
depends_on = None


def columns(mutable=False):
    result = [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]
    if mutable:
        result += [
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ]
    return result


def upgrade():
    op.create_table(
        "damage_drafts",
        *columns(True),
        sa.Column("pending_claims", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("focused_item_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="collecting"),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["reception_sessions.tenant_id", "reception_sessions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "user_id"],
            ["orders.tenant_id", "orders.id", "orders.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "focused_item_id", "order_id"],
            ["order_items.tenant_id", "order_items.id", "order_items.order_id"],
        ),
        sa.UniqueConstraint("tenant_id", "session_id", "order_id"),
        sa.UniqueConstraint("tenant_id", "id", "order_id", name="uq_damage_draft_order"),
        sa.CheckConstraint("status IN ('collecting', 'ready_for_review')", name="draft_status"),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint("jsonb_typeof(pending_claims) = 'array'", name="pending_claims_array"),
    )
    op.create_table(
        "damage_draft_items",
        *columns(True),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("last_message_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id", "order_id"],
            ["damage_drafts.tenant_id", "damage_drafts.id", "damage_drafts.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_item_id", "order_id"],
            ["order_items.tenant_id", "order_items.id", "order_items.order_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "last_message_id"],
            ["reception_messages.tenant_id", "reception_messages.id"],
        ),
        sa.UniqueConstraint("tenant_id", "draft_id", "order_item_id"),
        sa.UniqueConstraint("tenant_id", "id", "draft_id", name="uq_damage_item_draft"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="positive_quantity"),
        sa.CheckConstraint(
            "description IS NULL OR length(btrim(description)) BETWEEN 1 AND 500",
            name="description_length",
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
    )
    op.create_table(
        "damage_attachments",
        *columns(),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("draft_item_id", sa.Uuid(), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("verification_status", sa.String(20), nullable=False, server_default="pending"),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_item_id", "draft_id"],
            [
                "damage_draft_items.tenant_id",
                "damage_draft_items.id",
                "damage_draft_items.draft_id",
            ],
        ),
        sa.UniqueConstraint("tenant_id", "draft_item_id", "content_hash"),
        sa.CheckConstraint("media_type IN ('image/jpeg', 'image/png')", name="image_type"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint("octet_length(content) BETWEEN 1 AND 5242880", name="image_size"),
        sa.CheckConstraint("verification_status = 'pending'", name="unreviewed_evidence"),
    )

    op.create_index("ix_damage_attachments_draft", "damage_attachments", ["tenant_id", "draft_id"])


def downgrade():
    op.drop_table("damage_attachments")
    op.drop_table("damage_draft_items")
    op.drop_table("damage_drafts")
