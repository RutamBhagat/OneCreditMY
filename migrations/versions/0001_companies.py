"""Create the company table and cosine index."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "0001_companies"
down_revision = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    companies = op.create_table(
        "companies",
        sa.Column("registration_number", sa.Text(), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("industry", sa.Text()),
        sa.Column("state", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("embedding", VECTOR(384)),
        if_not_exists=True,
    )
    op.create_index(
        "companies_active_embedding_hnsw",
        "companies",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=(companies.c.active == sa.true()) & companies.c.embedding.is_not(None),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("companies")
