# Test report — 2026-10-12

This report distinguishes executed checks from configuration, simulations and untested integration. No corporate meeting, tenant credential or private audio was used.

## Passed

### Assembled application, lightweight suite

- `python -m pytest -q`: **94 passed, 2 skipped** in the base application environment. The two skips are opt-in neural/model/API tests, executed separately below.
- Includes authentication before request-body consumption, distinct operator/inference credentials, upload/format/size/consent controls, real FFmpeg decoding, durable SQLite claims/recovery, queue bounds, optimistic transcript edits, safe filename handling, Unicode exports, meeting gate/URL checks, known/unknown session stop states, stop-during-join race, authoritative snapshot replacement and preservation of operator edits.
- Most connector/model contract tests use explicit fakes/HTTP mock transports. They do not demonstrate actual Teams joining or meeting quality.
- Independent review re-ran the suite and verified unauthorized/chunked oversized requests fail without stored files.

### Real local neural inference and full API pipeline

Actual official GigaAM Multilingual 220M CTC weights were downloaded and verified. Native implementation revision: `7447938d791c4f3e643386ee22c33777004293a5`. HF weights/config revision: `2f8a57144e6ec3adfd32fe0484d9ea9913305bc8`. Checkpoint SHA256: `e1db43873ec5e296f229572e06e2470fc157ac9f8d4aacabda295630b9b91728`.

- Linux x86_64, Python 3.12, PyTorch/torchaudio 2.10.0+cpu.
- [Official GigaAM example](https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/example.wav), 11.29 seconds, Russian. The output matched the upstream expected transcription exactly.
- Cold standalone smoke: approximately 7.1 seconds and 1.91 GiB peak process RSS, with two inference threads. This is not a real-time/concurrency guarantee.
- A 33.87-second repetition test exercised actual two-window inference and overlap ownership. The tested split retained all expected words without a duplicated seam.
- A 10 ms nonzero clip handled safely; exact digital silence skipped inference.
- Full real upload → SQLite queued job → worker → local GigaAM → completed transcript → TXT/MD/DOCX/PDF passed. Extracted text in all formats matched the neural transcript. Database reopen preserved the result; uploaded and normalized temporary audio were removed.
- Full API smoke wall time: 8.725 seconds for the sample. Export sizes: TXT 929 B; MD 1,015 B; DOCX 37,448 B; PDF 26,686 B.
- The real `/v1/audio/transcriptions` request returned the same text and correct WAV duration, 11.29 seconds.
- Real neural tests ran with Python network connections/URL downloads patched to fail. This verifies the tested inference path does not download or call a speech service, not a production packet-capture audit of every component.
- Dedicated ASR suite with provisioned model/sample: **33 passed**, including both opt-in real checks. See [ASR.md](ASR.md) for reproduction and model caveats.

### Teams adapter and patched upstream source

CI found and prompted a reproducibility correction after the first publication: running `git apply` inside `vendor/vexa` under an enclosing Git checkout silently skipped all patch paths. The unchanged SHA256 verifier correctly rejected the untouched source. The vendor script now applies the reviewed patch to the index of its own isolated temporary Git checkout, then exports that patched tree. Two offline regression cases execute the actual script inside/outside a parent Git checkout (including a path with spaces). Both pass, and a fresh fetch of the actual pinned Vexa source into a nested destination passes all 15 hardened-file checks. No privacy hash, patch or verification assertion was weakened.

- Connector suite: **33 passed**, including source verification when the pinned vendor snapshot is present.
- Official Vexa revision `f1a62fbbdfd2c98cb162582d82c31fe8102acc89` fetched successfully. Mandatory local privacy patch applied; changed-file SHA256 manifest verified.
- Patched Vexa TypeScript capture modules, bot dependencies and browser bundle compiled successfully using the upstream build.
- Source regression checks cover hard-off raw fixture capture, exact local ASR endpoint, no identity override, no cloud captions, no stealth/fingerprint manipulation, explicit meeting ceiling and source identity. They do not substitute for Docker/live-wire behavior verification.

### Interface, exports and static checks

- Unicode TXT/MD, DOCX XML and PDF text extraction tests passed, including Kazakh-specific Cyrillic characters and inert markup inputs.
- Dashboard jsdom interaction smoke: **21 scenarios passed, 32 stubbed API calls, zero jsdom errors**; with contract-stubbed API responses; covers login/cancel/reopen, consent, repeated submits, both create paths, source-aware status/stop/retry, history/search, inert XSS, Unicode editing, conflict/interrupted edits, copy, four authenticated downloads, stale-response races and logout. See [UI.TESTING.md](UI.TESTING.md).
- `ruff check app tests scripts deploy/vexa`, Python compilation, `node --check app/static/app.js`, shell syntax, YAML parsing and `uv lock --check` passed.
- Python requirements are pinned; real ASR lock installation was validated on CPU. Official CPU/CUDA torch wheels are installed before the common lock so the generic exact version preserves the chosen wheel variant.

## Not run / blocked

- **Real Teams meeting join, lobby admission/rejection, organizer removal, participant consent revocation, SSO/CAPTCHA handling, live audio delivery, stop propagation and transcript completeness:** no approved tenant/test meeting credentials; connector remains disabled by default.
- **Docker image builds/Compose runtime, container networking, Vexa bootstrap, browser isolation, PostgreSQL/Valkey persistence and restart behavior:** no Docker engine in the build workspace. YAML parsing and source compilation are narrower checks.
- **GPU inference and optional faster-whisper neural execution:** no compatible GPU tested; only adapter contracts for optional backend.
- **Kazakh/English/codeswitching accuracy and multi-speaker meeting quality:** no suitable authorized held-out dataset used. Russian sample success is not evidence of all-language quality.
- **Visual browser QA:** local Playwright Chromium could not start because sandbox networking denied its socket creation; no page rendered. DOM smoke is simulation, not a screenshot/accessibility/browser-engine audit. Do not infer a visual pass.
- **Production security/availability:** no penetration test, load/capacity test, disaster-recovery drill, legal review or Microsoft certification.
- The dedicated neural environment (which does not need pytest-asyncio for its synchronous tests) also emits an unused asyncio_mode configuration warning. Base tests emit an upstream Starlette warning about future httpx test-client support. Runtime/API behavior passed; the warning is not suppressed.

## Pilot acceptance required

Use a specifically approved disposable Teams meeting with notified participants. Verify ordinary lobby admission, visible bot identity, local-only effective inference route, transcript completeness, explicit rejection/stop, concurrent-client edit conflicts, ASR outage, duplicate submission, server/bot restart and every retained copy. Establish a per-language accuracy and latency threshold before expanding use. Do not enable the connector solely because the source tests passed.

## Easy-start update (2026-10-12)

- Added Windows `START`, `STOP`, `CHECK`, `LOGIN-TOKEN` and explicitly approved `IT/PREPARE` launchers. Ordinary start never builds/pulls an image or downloads model files; hashes are checked against the runtime's pinned model manifest.
- Added authenticated `/api/readiness` and a Russian dashboard preflight panel. These report prerequisites only, not successful neural inference, model initialization, a reachable Teams tenant or a tested live join. No private paths, tokens or connector URLs are returned.
- Fresh pinned Vexa source preparation and its integrity/privacy verifier passed. Aggregate Python suite: **127 passed, 2 skipped** (opt-in real neural tests were not rerun for this update). Ruff and JavaScript syntax checks passed. Dashboard DOM smoke passed with readiness checks, **39 stubbed API calls**, zero jsdom errors.
- Windows PowerShell mock contract tests are included in CI. They cover explicit/cancelled provisioning, random distinct tokens, retained existing credentials, missing/bad-hash models, unsupported Teams booleans, no implicit downloads on Start, cancelled token/stop actions, stop after broken Teams settings and remote Docker rejection. This executor has neither PowerShell nor Docker; these Windows tests and real Windows/Docker launch were **not run locally**. CI must pass for the published commit; even a green mocked job is not a live container test.
- The source ZIP includes all three notebooks, scripts, deployment files and a per-file SHA256 manifest. It deliberately excludes credentials, container images, model weights, meeting recordings/databases and untracked local files. IT must provision runtime dependencies separately; this ZIP alone cannot operate offline.
