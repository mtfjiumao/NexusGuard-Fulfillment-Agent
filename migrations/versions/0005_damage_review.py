"""Persist submitted damage materials and per-item review decisions.

Revision ID: 0005_damage_review
Revises: 0004_damage
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_damage_review"
down_revision = "0004_damage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("damage_attachments", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "damage_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("notice", sa.Text()),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"], ["damage_drafts.tenant_id", "damage_drafts.id"]
        ),
        sa.UniqueConstraint("tenant_id", "draft_id", "revision"),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_supplement', 'rejected', 'passed', 'withdrawn')",
            name="review_status",
        ),
    )
    op.create_index(
        "ix_damage_review_queue", "damage_reviews", ["tenant_id", "status", "created_at", "id"]
    )
    op.create_index(
        "uq_damage_review_pending",
        "damage_reviews",
        ["tenant_id", "draft_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "damage_review_items",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("draft_item_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(450), nullable=False),
        sa.Column("purchased_quantity", sa.Integer(), nullable=False),
        sa.Column("damaged_quantity", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("photos", JSONB(), nullable=False),
        sa.Column("decision", sa.String(24)),
        sa.Column("reason", sa.String(500)),
        sa.Column("reviewer_id", sa.String(128)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "review_id"], ["damage_reviews.tenant_id", "damage_reviews.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_item_id"],
            ["damage_draft_items.tenant_id", "damage_draft_items.id"],
        ),
        sa.UniqueConstraint("tenant_id", "review_id", "draft_item_id"),
        sa.CheckConstraint(
            "damaged_quantity BETWEEN 1 AND purchased_quantity", name="valid_quantity"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(photos) = 'array' AND photos <> '[]'::jsonb", name="nonempty_photos"
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND reason IS NULL AND reviewer_id IS NULL AND reviewed_at IS NULL)"
            " OR (decision IS NOT NULL AND decision IN ('passed', 'needs_supplement', 'rejected')"
            " AND length(btrim(reason)) > 0 AND reason IS NOT NULL"
            " AND length(btrim(reviewer_id)) > 0 AND reviewer_id IS NOT NULL"
            " AND reviewed_at IS NOT NULL)",
            name="decision_fields",
        ),
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM damage_reviews) OR EXISTS "
            "(SELECT 1 FROM damage_attachments WHERE removed_at IS NOT NULL)"
        )
    ):
        raise RuntimeError("Archive damage reviews and removed photos before downgrading")
    op.drop_table("damage_review_items")
    op.drop_table("damage_reviews")
    op.drop_column("damage_attachments", "removed_at")
