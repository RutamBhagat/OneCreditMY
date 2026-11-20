"""Create the AMQP event receipt table."""

import sqlalchemy as sa
from alembic import op

revision = "0002_event_receipts"
down_revision = "0001_companies"


def upgrade() -> None:
    op.create_table(
        "event_receipts",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column("raw_body", sa.LargeBinary()),
        sa.Column("rejection_reason", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("event_receipts")
