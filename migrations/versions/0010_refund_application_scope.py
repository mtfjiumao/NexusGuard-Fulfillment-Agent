"""Enforce a single financial action per approved application and action type."""

from alembic import op

revision = "0010_refund_application_scope"
down_revision = "0009_delay_compensation"
branch_labels = None
depends_on = None


def upgrade():
    # Existing actions keep their provider request IDs and historical idempotency keys.
    op.create_unique_constraint(
        "uq_financial_action_case_type",
        "financial_actions",
        ["tenant_id", "case_id", "action_type"],
    )


def downgrade():
    op.drop_constraint("uq_financial_action_case_type", "financial_actions", type_="unique")
