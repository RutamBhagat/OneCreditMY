from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, Index, LargeBinary, Text, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    registration_number: Mapped[str] = mapped_column(Text, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool]
    name: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(384))


class EventReceipt(Base):
    __tablename__ = "event_receipts"

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


Index(
    "companies_active_embedding_hnsw",
    Company.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_where=(Company.active == true()) & Company.embedding.is_not(None),
)
