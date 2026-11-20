"""Validate the registry feed before inference or company writes."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

MALAYSIA_TIME = timezone(timedelta(hours=8))
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class InvalidEvent(ValueError):
    """A delivery body that can be quarantined without retrying."""


def check_storable_text(value: str) -> str:
    if "\0" in value:
        raise ValueError("Text cannot contain a null character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Text must be valid UTF-8") from error
    return value


def check_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text cannot be blank")
    return value


StoredText = Annotated[str, AfterValidator(check_storable_text)]
NonblankText = Annotated[StoredText, AfterValidator(check_nonblank)]


class CompanyData(BaseModel):
    model_config = ConfigDict(strict=True)

    name: NonblankText = Field(alias="companyName")
    industry: StoredText | None = None
    state: StoredText | None = None
    description: StoredText | None = Field(default=None, alias="natureOfBusiness")


class RegistryEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    registration_number: NonblankText = Field(alias="registrationNumber")
    updated_at: datetime = Field(alias="updatedAt")

    @field_validator("registration_number")
    @classmethod
    def canonicalize_registration(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("updated_at", mode="before")
    @classmethod
    def parse_registry_timestamp(cls, value: object) -> datetime:
        if type(value) is int and 10**12 <= value < 10**13:
            return UNIX_EPOCH + timedelta(milliseconds=value)
        if isinstance(value, str):
            try:
                timestamp = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("Invalid ISO timestamp") from error
            return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=MALAYSIA_TIME)
        raise ValueError("Expected ISO text or 13-digit Unix milliseconds")


class CompanyUpserted(RegistryEvent):
    type: Literal["company-upserted"]
    data: CompanyData


class CompanyStruckOff(RegistryEvent):
    type: Literal["company-struck-off"]


EVENT = TypeAdapter(Annotated[CompanyUpserted | CompanyStruckOff, Field(discriminator="type")])


def parse_event(body: bytes) -> CompanyUpserted | CompanyStruckOff:
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise InvalidEvent(f"Invalid JSON ({error})") from error

    try:
        return EVENT.validate_python(payload)
    except ValidationError as error:
        reasons = []
        for item in error.errors(include_input=False, include_url=False, include_context=False):
            location = ".".join(map(str, item["loc"])) or "envelope"
            detail = item["msg"] if item["type"] == "value_error" else item["type"]
            reasons.append(f"{location} ({detail})")
        raise InvalidEvent("Invalid event at " + ", ".join(reasons)) from error
