# Experimental Teams capture connector

## What is implemented

A real adapter to the self-hosted [Vexa](https://github.com/Vexa-ai/vexa) browser worker,
pinned to `f1a62fbbdfd2c98cb162582d82c31fe8102acc89`, with a reviewed local-only
source patch. The original source is Apache-2.0; its license and third-party
inventory accompany this deployment. This is an experimental browser adapter,
not a Microsoft-supported Graph media bot and not a certification that browser
recording is permitted. No real meeting was joined during development.

A URL submitted to the local application becomes `POST /bots` at the local Vexa
gateway. A visible `Local transcription bot` requests ordinary anonymous entry.
After admission, browser audio is passed to the local ASR app as mono 16 kHz PCM16
WAV. The app's GigaAM/Whisper routing performs inference locally. Vexa maintains
transcript segments; the app polls the exact meeting row and stores/export them.
No hosted STT or LLM account is needed. Teams itself still needs internet access.

## Authorization gate (required, not satisfied by installing this software)

1. Obtain and record tenant/admin and platform-specific approval for this exact
   browser capture method. Review applicable terms, privacy requirements and
   participant notice/consent with the responsible people. An open-source license
   does not authorize use of Teams or collection of meeting content.
2. Keep `TEAMS_ENABLED=false` until that review is complete. Configure an
   actual approval record reference and explicit approved meeting IDs. A Teams
   URL's `context.Tid` is editable by its sender, so a tenant hint never authorizes
   an arbitrary meeting. An optional tenant list is an additional restriction.
3. For each meeting, the submitting operator must explicitly acknowledge
   authorization, participant notice and any required consent. The visible name
   is a notice aid, not proof of legally sufficient notice or consent.
4. Respect lobby decisions, SSO/guest policy, CAPTCHA, recording denial and any
   organizer instruction. This build has no authenticated session store, CAPTCHA
   solver, stealth plugin, host-denial retry, or alternate login route. If the
   meeting needs unsupported access, stop and use an approved workflow.

Microsoft's [media access guidance](https://learn.microsoft.com/en-us/graph/cloud-communications-media)
and [recording status API](https://learn.microsoft.com/en-us/graph/api/call-updaterecordingstatus?view=graph-rest-1.0)
impose requirements on Graph raw-media recording and derived data. This browser
adapter is not the Teams policy-based recording solution. Setting
`recording_enabled=false` only disables raw recording files; a transcript is still
derived meeting data. Do not treat that setting as permission or a policy bypass.

The browser cannot prove every recording-policy condition or detect every new
Teams UI. Consent revocation/recording denial is not comprehensively automated:
the operator must stop the bot immediately if permission is absent or withdrawn.
This unresolved production approval and detection work is why capture ships off.

## Minimal source-built deployment

Use a dedicated Linux Docker host or disposable VM, not a shared workstation or
corporate Docker daemon. The runtime mounts the Docker socket and can control the
host daemon. The browser presently inherits `--no-sandbox`; container isolation
is not a substitute for a dedicated security boundary. Do not expose the Vexa
admin API or gateway to the internet. Restrict network access to the approved
Teams endpoints and local services using your infrastructure controls.

From the project root:

```sh
./scripts/vendor_vexa.sh
python3 deploy/vexa/verify_vendor.py vendor/vexa
cp deploy/vexa/.env.example deploy/vexa/.env
# Populate unique VEXA_DB_PASSWORD, VEXA_ADMIN_TOKEN, VEXA_INTERNAL_SECRET,
# and the SAME ASR_API_TOKEN used by the application. Do not use example secrets.
docker compose --env-file deploy/vexa/.env -f deploy/vexa/compose.yaml --profile build-only build
# Start your local ASR app on the teams-asr-vexa network as alias asr-app:8000.
docker compose --env-file deploy/vexa/.env -f deploy/vexa/compose.yaml up -d
```

The `teams-asr-vexa` Docker network is created by this compose file. When using a
separate application compose file, attach the app to this external network with
alias `asr-app`. Start the Vexa infrastructure/network first if the external
network does not exist, then the app. Initial ASR health failure should clear once
the app is ready; inspect `/health` before any authorized meeting test.

The deployment builds only Postgres, Valkey, identity, runtime, meeting API,
gateway and a bot image. There is no Vexa agent/LLM, dashboard, VNC, MCP or object
storage service. The bot image is built from source on the official Playwright
image; the floating upstream `vexa/meet-join-env:dev` base is not used. Dependencies
remain third-party software. Source and model revisions are pinned, but base OS
images and package archives should be mirrored and digest-locked for a fully
reproducible production supply chain.

Provision a dedicated local Vexa identity only after approving persistent local
credential creation. Export the same `VEXA_ADMIN_TOKEN`, then manually run:

```sh
python3 deploy/vexa/bootstrap.py --confirm-local-credential-creation
```

The script writes a `bot`-scoped key to `data/vexa-api-key` with mode 0600. Configure
that value as the application's `VEXA_API_KEY`; set `VEXA_API_URL` to
`http://vexa-gateway:8000` on the shared network or `http://127.0.0.1:18056` on the
host. This script was not run during development. Do not expose or send the key.

For troubleshooting, identity is at `127.0.0.1:18057`, gateway at
`127.0.0.1:18056`. Both bind only to loopback. Service health checks are liveness
checks, not proof that Teams admission or ASR works. First validate local audio
upload/transcription, then use a specifically approved test meeting with notified
participants. A build or mock test does not establish real-meeting success.

## Privacy fork: do not substitute an unpatched upstream image

`deploy/vexa/local-only.patch` changes the pinned source, and
`verify_vendor.py` verifies hashes of every changed source file:

- Hard-disables Vexa's independently default-on captured-signal fixture tape,
  even if identity is unavailable or returns `capture_signal=true`. This tape
  otherwise captures raw audio even with `recording_enabled=false`.
- Removes recording upload capability and rejects raw recording or authenticated
  browser invocations in the meeting service and bot.
- Ignores per-user STT URL/token/model overrides. Both service and bot require
  exactly `http://asr-app:8000` and a bearer token. The ASR service never falls back
  to a hosted provider.
- Disables Teams cloud-caption activation and reading of already-enabled
  captions. Page console and DOM speaker diagnostics are not copied into logs.
- Removes stealth/fingerprint spoofing and disabled site-isolation flags. Normal
  automation remains visible to the platform.
- Disables automatic rejoin classification. Adds a documented private
  `automaticLeave.maxBotTime` schema field so the request's maximum duration
  reaches the browser's active-phase ceiling instead of being silently dropped.
- Pins auxiliary pyannote to revision `733a93b6473d019a773298e08cefa686894b1854`,
  verifies SHA-256 of its FP32 weights and configs, and loads only the build-time cache at runtime.
  A missing/unloadable model fails the image build or capture; it never switches
  to hosted STT. The auxiliary segmenter is not GigaAM or an additional LLM.

Transcripts intentionally persist in the application SQLite and in Vexa
Postgres/Valkey. Deleting an app job alone does not erase Vexa's copy. Apply the
organization's retention/deletion procedures to all three stores and backups.
Local diagnostic logs can still contain meeting IDs and failure metadata; keep
them private and short-lived. Browser profiles are ephemeral but crash residues
and host/Docker storage need operational cleanup. This is not a zero-retention
claim.

## Verified wire contract

All gateway requests use `X-API-Key`. Create:

```json
{"platform":"teams","meeting_url":"<approved direct URL>","bot_name":"Local transcription bot","language":"kk","recording_enabled":false,"transcribe_enabled":true,"automatic_leave":{"max_bot_time":7200000,"max_wait_for_admission":300000,"max_time_left_alone":60000}}
```

`language` is omitted for `auto`. Create returns `id`, `native_meeting_id`,
`platform`, and `status`. Poll `GET /transcripts/by-id/{id}`, never the native-link
transcript path: the latter can select the newest occurrence of a recurring link.
Results contain `status`, `start_time`, `data`, and `segments` with
`start/end/text/speaker/language/segment_id/completed`. Some capture lanes return
epoch timestamps; the adapter converts them using the meeting start time and
rejects an unanchored epoch instead of corrupting SRT offsets. Live segments can
be revised or retracted, so the app replaces snapshots, not appends blindly.
Speaker labels are browser/diarizer cues and can be wrong, particularly with
overlap; they are not verified identity.

The ASR seam is `POST /v1/audio/transcriptions`, `Authorization: Bearer
<ASR_API_TOKEN>`, multipart `file` (WAV), `model=local`,
`response_format=verbose_json`, optional `language`,
`timestamp_granularities=word`, and optional `prompt`/VAD fields. Response:
`text/language/duration/segments[{start,end,text,words?}]`. Local engines must not
invent confidence or word alignment. Vexa can consume text-only segment results;
short chunk timing is approximate and needs real audio validation.

## Lifecycle, restart and cancellation

The connector commits a join-attempt journal before POSTing. Retries with the
same key return the stored handle, including after app restart. If create timed
out or the app crashed before storing the response, the result is `needs_review`.
The adapter lists possible new rows for an operator but never blindly reposts or
adopts an unrelated bot. Keep a dedicated Vexa account and preserve the journal.

Stop uses `DELETE /bots/teams/{native_meeting_id}`. Upstream this stops every
active row for that room under the account, so the adapter checks that no other
active row exists. Missing/ambiguous ownership requires operator review. A
repeated stop/404 can mean teardown is pending, not finished: continue polling
until the exact row is terminal. Unknown statuses and missing rows fail closed.
The worker must handle a cancel that arrives during create by stopping the
returned handle before any further work. At `needs_help`, request safe stop and
require review; never solve an access challenge.

## Validation boundaries

Offline tests cover URL/host spoofing, forged tenant hints, config and per-meeting
gates before network access, REST payloads, exact-row transcripts, timestamps,
restart idempotency, ambiguous joins, rejected admission and idempotent/scope-safe
stop. The privacy verifier checks exact patched source and exercises the hard-off
fixture policy. See the project's test report for executed counts and build
results. Docker image launch, real Teams UI, lobby behavior, traffic capture,
concurrency isolation and live transcription quality require the authorized
acceptance run. They have not been inferred from mocks or repository claims.
