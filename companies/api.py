"""Similar-company API and browser demo."""

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from scalar_fastapi import AgentScalarConfig, add_scalar_reference
from sqlalchemy import create_engine, select, true
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.pool import NullPool

from companies.db import database_url
from companies.models import Company

app = FastAPI()
add_scalar_reference(app, agent=AgentScalarConfig(disabled=True))
engine = create_engine(database_url(), poolclass=NullPool)
assets = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=assets / "templates")
app.mount("/static", StaticFiles(directory=assets / "static"), name="static")
DEMO_COMPANY = "SYN-001550"
DEFAULT_LIMIT = 4


def similar_companies_statement(registration_number: str, limit: int, *, include_source: bool = False):
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
    statement = (
        select(neighbors.c.registration_number, neighbors.c.name, neighbors.c.industry, neighbors.c.state)
        .select_from(source)
        .outerjoin(neighbors, true())
        .where(source.registration_number == registration_number, source.active == true())
        .order_by(neighbors.c.distance.asc(), neighbors.c.registration_number.asc())
    )
    if include_source:
        statement = statement.add_columns(
            source.registration_number.label("source_registration_number"),
            source.name.label("source_name"),
            source.industry.label("source_industry"),
            source.state.label("source_state"),
            source.description.label("source_description"),
        )
    return statement


class CompanyCard(BaseModel):
    registration_number: str
    name: str
    industry: str | None
    state: str | None


def recommendation_rows(registration_number: str, limit: int, *, include_source: bool = False):
    statement = similar_companies_statement(registration_number.strip().upper(), limit, include_source=include_source)
    try:
        with Session(engine) as session:
            return session.execute(statement).mappings().all()
    except DBAPIError as error:
        failed_to_connect = isinstance(error, OperationalError) and error.statement is None
        if not (failed_to_connect or error.connection_invalidated):
            raise
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from error


@app.get(
    "/companies/{registration_number}/similar",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unavailable"}},
)
def similar_companies(
    registration_number: str, limit: Annotated[int, Query(ge=1, le=50)] = DEFAULT_LIMIT
) -> list[CompanyCard]:
    rows = recommendation_rows(registration_number, limit)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return [CompanyCard(**row) for row in rows if row["registration_number"] is not None]


@app.get("/", include_in_schema=False)
def home(request: Request) -> RedirectResponse:
    return RedirectResponse(request.url_for("demo_company", registration_number=DEMO_COMPANY).path)


@app.get("/demo/{registration_number:path}", response_class=HTMLResponse, include_in_schema=False)
def demo_company(request: Request, registration_number: str) -> HTMLResponse:
    rows = recommendation_rows(registration_number, DEFAULT_LIMIT, include_source=True)
    return templates.TemplateResponse(
        request=request,
        name="company.html",
        context={
            "source": rows[0] if rows else None,
            "cards": [row for row in rows if row["registration_number"] is not None],
            "demo_root": request.url_for("demo_company", registration_number="").path,
            "demo_company_number": DEMO_COMPANY,
        },
        status_code=status.HTTP_200_OK if rows else status.HTTP_404_NOT_FOUND,
    )
