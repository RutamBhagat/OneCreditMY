"""HTTP endpoint for similar-company cards."""

from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel
from scalar_fastapi import AgentScalarConfig, add_scalar_reference
from sqlalchemy import create_engine, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.pool import NullPool

from companies.db import database_url
from companies.models import Company

app = FastAPI()
add_scalar_reference(app, agent=AgentScalarConfig(disabled=True))
engine = create_engine(database_url(), poolclass=NullPool)


def similar_companies_statement(registration_number: str, limit: int):
    source = aliased(Company, name="source")
    distance = Company.embedding.cosine_distance(source.embedding)
    neighbors = (
        select(Company.registration_number, Company.name, Company.industry, Company.state, distance.label("distance"))
        .where(
            Company.active == true(),
            Company.embedding.is_not(None),
            source.embedding.is_not(None),
            Company.registration_number != source.registration_number,
        )
        .order_by(distance.asc())
        .limit(limit)
        .correlate(source)
        .lateral("neighbors")
    )
    return (
        select(neighbors.c.registration_number, neighbors.c.name, neighbors.c.industry, neighbors.c.state)
        .select_from(source)
        .outerjoin(neighbors, true())
        .where(source.registration_number == registration_number, source.active == true())
        .order_by(neighbors.c.distance.asc(), neighbors.c.registration_number.asc())
    )


class CompanyCard(BaseModel):
    registration_number: str
    name: str
    industry: str | None
    state: str | None


@app.get("/companies/{registration_number}/similar")
def similar_companies(registration_number: str, limit: Annotated[int, Query(ge=1, le=50)] = 4) -> list[CompanyCard]:
    statement = similar_companies_statement(registration_number.strip().upper(), limit)
    with Session(engine) as session:
        rows = session.execute(statement).mappings().all()
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return [CompanyCard(**row) for row in rows if row["registration_number"] is not None]
