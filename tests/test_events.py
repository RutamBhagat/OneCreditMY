import json

import pytest

from companies.events import InvalidEvent, parse_event


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"{", "Invalid JSON"),
        (b"\xff", "Invalid JSON"),
        (b"null", "envelope"),
        (b"[]", "envelope"),
        (b"{}", "union_tag_not_found"),
    ],
)
def test_rejects_malformed_envelopes(body, reason):
    with pytest.raises(InvalidEvent, match=reason):
        parse_event(body)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"type": "company-updated"}, "union_tag_invalid"),
        ({"registrationNumber": 123}, "registrationNumber.*string_type"),
        ({"registrationNumber": " \t"}, "registrationNumber.*blank"),
        ({"updatedAt": "yesterday"}, "updatedAt.*Invalid ISO timestamp"),
        ({"updatedAt": 1786680000}, "updatedAt.*13-digit Unix milliseconds"),
        ({"data": None}, "data.*model_type"),
        ({"data": {}}, "companyName.*missing"),
        ({"data": {"companyName": "\t"}}, "companyName.*blank"),
        ({"data": {"companyName": "Café", "industry": 123}}, "industry.*string_type"),
        ({"data": {"companyName": "Café\0"}}, "companyName.*null character"),
        ({"data": {"companyName": "Café", "state": "\ud800"}}, "state.*valid UTF-8"),
    ],
)
def test_rejects_invalid_upsert_fields(fields, reason):
    event = {
        "type": "company-upserted",
        "registrationNumber": "SYN-000001",
        "updatedAt": "2026-08-14T09:00:00+08:00",
        "data": {"companyName": "Café"},
    }
    with pytest.raises(InvalidEvent, match=reason):
        parse_event(json.dumps(event | fields).encode())


def test_accepts_sparse_upsert_with_unknown_envelope_fields():
    event = parse_event(
        b'{"type":"company-upserted","registrationNumber":" syn-000001 ",'
        b'"updatedAt":"2026-08-14T01:00:00Z","data":{"companyName":"Cafe","industry":null},'
        b'"registryMetadata":{"version":2}}'
    )
    assert event.registration_number == "SYN-000001"
    assert event.updated_at.isoformat() == "2026-08-14T01:00:00+00:00"
    assert event.data.model_dump() == {"name": "Cafe", "industry": None, "state": None, "description": None}
    assert "registryMetadata" not in event.model_dump()
