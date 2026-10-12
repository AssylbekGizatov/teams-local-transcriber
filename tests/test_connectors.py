"""Offline tests: no real meeting, credentials, browser, or external ASR."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.connectors.vexa import (
    ConnectorDisabled,
    ConnectorError,
    ConnectorPolicyError,
    VexaConfig,
    VexaConnector,
    normalize_segments,
    parse_teams_url,
)

NATIVE = "19:meeting_QWxwaGE=@thread.v2"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
URL = (
    "https://teams.microsoft.com/l/meetup-join/"
    + quote(NATIVE, safe="")
    + "/0?context="
    + quote(json.dumps({"Tid": TENANT}))
)
SHORT = "https://teams.microsoft.com/meet/1234567890123?p=private-passcode"


def config(tmp_path, **kw):
    defaults = dict(
        base_url="http://vexa-gateway:8000",
        api_key="test-key",
        enabled=True,
        authorization_reference="APPROVAL-TEST-ONLY",
        allowed_meeting_ids=(NATIVE,),
        state_path=tmp_path / "connector.sqlite3",
    )
    defaults.update(kw)
    return VexaConfig(**defaults)


def row(id=7, status="active", native=NATIVE, **kw):
    return dict(id=id, platform="teams", native_meeting_id=native, status=status, **kw)


class Wire:
    def __init__(self):
        self.calls = []
        self.rows = []
        self.doc = row(start_time="2026-10-12T10:00:00Z", segments=[])
        self.post_failure = None
        self.delete_count = 0

    def handle(self, request):
        self.calls.append(request)
        assert request.headers["X-API-Key"] == "test-key"
        if request.method == "GET" and request.url.path == "/bots":
            return httpx.Response(200, json={"meetings": self.rows, "has_more": False})
        if request.method == "POST":
            if self.post_failure:
                raise self.post_failure
            self.rows = [row(status="requested")]
            return httpx.Response(201, json=self.rows[0])
        if request.method == "DELETE":
            self.delete_count += 1
            self.rows = [row(status="stopping", data={"stop_requested": True})]
            return httpx.Response(200 if self.delete_count == 1 else 404, json={})
        if request.url.path == "/transcripts/by-id/7":
            return httpx.Response(200, json=self.doc)
        return httpx.Response(404)

    def connector(self, tmp_path, **kw):
        return VexaConnector(
            config(tmp_path, **kw),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )


def test_parse_direct_links():
    assert parse_teams_url(URL)["native_meeting_id"] == NATIVE
    assert parse_teams_url(URL)["tenant_id"] == TENANT
    assert parse_teams_url(SHORT)["native_meeting_id"] == "1234567890123"


@pytest.mark.parametrize(
    "url",
    [
        "http://teams.microsoft.com/meet/123456",
        "https://teams.microsoft.com.evil.example/meet/123456",
        "https://user@teams.microsoft.com/meet/123456",
        "https://teams.microsoft.com:444/meet/123456",
        "https://127.0.0.1/meet/123456",
        "https://teams.microsoft.com/meet/123456#fragment",
        "https://teams.microsoft.com/meet/abc",
        "https://teams.microsoft.com/meet/123456\n",
        "https://teams.microsoft.com/meet/123456?context=not-json",
        "https://teams.microsoft.com.evil.com/l/meetup-join/abc/0",
        "https://teams.microsoft.com/meet/%2f123456",
        "https://teams.microsoft.com/l/meetup-join/19:meeting_a%2fb@thread.v2/0",
    ],
)
def test_reject_unsafe_links(url):
    with pytest.raises(ConnectorPolicyError):
        parse_teams_url(url)


@pytest.mark.asyncio
async def test_gates_precede_all_io(tmp_path):
    wire = Wire()
    for overrides, consent in [
        ({"enabled": False}, True),
        ({"authorization_reference": ""}, True),
        ({"allowed_meeting_ids": ()}, True),
        ({}, False),
        ({"bot_name": "Silent helper"}, True),
    ]:
        connector = wire.connector(tmp_path, **overrides)
        with pytest.raises((ConnectorDisabled, ConnectorPolicyError)):
            await connector.join(URL, "test", "ru", "job-1", consent)
    assert wire.calls == []
    assert not (tmp_path / "connector.sqlite3").exists()


@pytest.mark.asyncio
async def test_tenant_hint_does_not_authorize_arbitrary_meeting(tmp_path):
    connector = Wire().connector(
        tmp_path, allowed_tenant_ids=(TENANT,), allowed_meeting_ids=("some-approved-meeting",)
    )
    with pytest.raises(ConnectorPolicyError):
        await connector.join(URL, "test", "ru", "job-1", True)


@pytest.mark.asyncio
async def test_payload_and_exact_row_poll(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "kk", "job-1", True)
    payload = json.loads(wire.calls[-1].content)
    assert payload["platform"] == "teams" and payload["meeting_url"] == URL
    assert payload["recording_enabled"] is False and payload["transcribe_enabled"] is True
    assert payload["language"] == "kk" and "transcription" in payload["bot_name"]
    assert payload["automatic_leave"]["max_bot_time"] == 7_200_000
    assert handle["meeting_id"] == 7 and handle["uncertain"] is False
    result = await connector.poll(handle)
    assert result["status"] == "active"
    assert wire.calls[-1].url.path == "/transcripts/by-id/7"


@pytest.mark.asyncio
async def test_idempotency_survives_restart(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    first = await connector.join(URL, "Review", "ru", "stable-key", True)
    restarted = wire.connector(tmp_path)
    second = await restarted.join(URL, "Review", "ru", "stable-key", True)
    assert second == first
    assert sum(r.method == "POST" for r in wire.calls) == 1
    with pytest.raises(ConnectorPolicyError):
        await restarted.join(URL, "Different", "ru", "stable-key", True)


@pytest.mark.asyncio
async def test_ambiguous_timeout_never_reposts(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    wire.post_failure = httpx.ReadTimeout("uncertain")
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    assert handle["uncertain"] is True
    wire.post_failure = None
    wire.rows = [row(id=99)]
    restarted = wire.connector(tmp_path)
    assert await restarted.join(URL, "Review", "ru", "job-1", True) == handle
    status = await restarted.poll(handle)
    assert status["status"] == "needs_review" and status["candidate_meeting_ids"] == [99]
    assert sum(r.method == "POST" for r in wire.calls) == 1
    with pytest.raises(ConnectorPolicyError):
        await restarted.stop(handle)


@pytest.mark.asyncio
async def test_existing_bot_is_not_adopted(tmp_path):
    wire = Wire()
    wire.rows = [row(id=99)]
    with pytest.raises(ConnectorPolicyError):
        await wire.connector(tmp_path).join(URL, "Review", "ru", "job-1", True)
    assert all(r.method == "GET" for r in wire.calls)


@pytest.mark.asyncio
async def test_auto_omits_language_and_auth_denial_does_not_leak(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    await connector.join(URL, "Review", "auto", "job-1", True)
    assert "language" not in json.loads(wire.calls[-1].content)
    denied = VexaConnector(
        config(tmp_path),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(403, text="super-private-secret")
            )
        ),
    )
    with pytest.raises(ConnectorPolicyError) as err:
        await denied._meetings(NATIVE)
    assert "super-private-secret" not in str(err.value)


@pytest.mark.asyncio
async def test_stop_repeated_waits_for_confirmed_exit(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    assert (await connector.stop(handle))["status"] == "stopping"
    assert (await connector.stop(handle))["status"] == "stopping"
    wire.rows = [row(status="completed")]
    assert (await connector.stop(handle))["already_stopped"] is True


@pytest.mark.asyncio
async def test_stop_does_not_affect_other_active_row(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    wire.rows += [row(id=99)]
    with pytest.raises(ConnectorPolicyError):
        await connector.stop(handle)
    assert wire.delete_count == 0


@pytest.mark.asyncio
async def test_disabled_connector_still_allows_owned_stop(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    disabled = VexaConnector(
        dataclasses.replace(connector.config, enabled=False), client=connector._client
    )
    assert (await disabled.stop(handle))["status"] == "stopping"


@pytest.mark.asyncio
async def test_handle_cannot_be_forged(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    with pytest.raises(ConnectorPolicyError):
        await connector.stop({**handle, "meeting_id": 99})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "awaiting_admission_rejected",
        "auth_session_missing",
        "join_failure",
        "awaiting_admission_timeout",
    ],
)
async def test_no_rejoin_after_denial(tmp_path, reason):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    wire.doc.update(status="completed", data={"completion_reason": reason})
    assert (await connector.poll(handle))["status"] == "failed"
    assert sum(r.method == "POST" for r in wire.calls) == 1


def test_epoch_and_relative_timestamp_contract():
    doc = {
        "start_time": "2026-10-12T10:00:00Z",
        "segments": [
            {
                "start": 1791799201.25,
                "end": 1791799202.5,
                "text": "Сәлем",
                "speaker": "Speaker A",
                "completed": False,
            }
        ],
    }
    result = normalize_segments(doc)[0]
    assert result["start"] == 1.25 and result["end"] == 2.5 and result["final"] is False
    assert (
        normalize_segments({"segments": [{"start": 1, "end": 2, "text": "Hello"}]})[0]["start"] == 1
    )
    with pytest.raises(ConnectorError):
        normalize_segments({"segments": doc["segments"]})
    with pytest.raises(ConnectorError):
        normalize_segments({"segments": [{"start": 1, "end": float("nan"), "text": "bad"}]})


def test_vendored_privacy_patch_when_present():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    vendor = root / "vendor/vexa"
    if not vendor.exists():
        pytest.skip("Optional upstream source not materialized; run scripts/vendor_vexa.sh")
    spec = importlib.util.spec_from_file_location(
        "verify_vexa", root / "deploy/vexa/verify_vendor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify(vendor)


@pytest.mark.asyncio
async def test_authoritative_only_for_exact_transcript(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    assert (await connector.poll(handle))["authoritative"] is True


@pytest.mark.asyncio
async def test_active_blocked_reason_requires_safe_stop(tmp_path):
    wire = Wire()
    connector = wire.connector(tmp_path)
    handle = await connector.join(URL, "Review", "ru", "job-1", True)
    wire.doc.update(status="active", data={"completion_reason": "join_failure"})
    assert (await connector.poll(handle))["status"] == "needs_review"


@pytest.mark.asyncio
async def test_uncertain_attempt_blocks_replacement_key(tmp_path):
    wire = Wire()
    wire.post_failure = httpx.ReadTimeout("uncertain")
    connector = wire.connector(tmp_path)
    await connector.join(URL, "Review", "ru", "job-1", True)
    with pytest.raises(ConnectorPolicyError):
        await connector.join(URL, "Review", "ru", "replacement-key", True)
    assert sum(r.method == "POST" for r in wire.calls) == 1
