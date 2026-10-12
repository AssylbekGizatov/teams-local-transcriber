# Operations

## Easy-start operations

For the three-step Windows operator path and separate IT provisioning, see [EASY_START.md](EASY_START.md). This is the full local web application; notebooks are optional experiments and are not required at runtime.

The easy-start launcher uses a pre-provisioned CPU image and read-only local models. Everyday startup must not build an image, install dependencies or download missing models. `easy-start/CHECK.cmd` checks prerequisites, configuration and pinned model hashes; it is not a successful neural inference test. `easy-start/STOP.cmd` asks the operator to reconcile active/ambiguous Teams sessions and type `STOP`, then stops the application without deleting persisted data or model files. The prompt is an operator confirmation, not an automated bot-exit check. Stopping this application does not establish that a separately deployed Vexa bot has left Teams.

IT owns the one-time preparation, Docker approval/installation, artifact provenance, host security, resource sizing and operational acceptance. The source ZIP does not include a runnable image or weights. For restricted corporate workstations, provide an authorized service URL through a managed TLS/identity-aware proxy rather than asking users to install tools or weaken policy. The default local listener remains loopback-only.

Do not run the easy-start stack alongside another application profile using the same port or database. Keep one queue owner. Before replacing an image or model, stop admission, finish/reconcile work, back up data and follow the upgrade policy below. Preserve local `.env` secrets separately from source archives; do not regenerate them casually during upgrades.

## Deployment shape

One Uvicorn process, one durable dispatcher, one local inference model, SQLite WAL and local files. Vexa supplies isolated browser workers plus its own PostgreSQL/Redis stack. Vexa is optional for file ingestion. CPU/GPU Compose profiles are alternatives, not concurrent replicas. GPU profile requires NVIDIA Container Toolkit and a driver compatible with the official CUDA 12.8 wheels.

Multipart spooling uses the private persistent-volume `/data/tmp` directory, not the 256 MB `/tmp` tmpfs. Budget space for parser spool, copied input and normalized PCM concurrently, plus queued files; per-file limits are not an aggregate disk quota. Monitor free space and stop admission before disk exhaustion.

The Teams browser needs Microsoft connectivity; local inference is offline after provisioning. This does not make Teams itself air-gapped. Keep the ASR service unreachable outside the private network. Put all exported service ports on loopback and use an authorized proxy for remote operators.

## Health and readiness

- `/healthz` only verifies the HTTP process responds.
- `/api/config` reports model path presence and connector enabled configuration, not neural warmup or tenant approval.
- Verify readiness with an authorized short sample and inspect job completion.
- Status polling and errors are visible per meeting. `needs_review` deliberately requires human reconciliation; a retry button is not a safe substitute for knowing whether a browser bot remains in a room.
- The default server disables access logging. Operational warnings use IDs and exception classes. Upstream logs should be treated as confidential metadata.

## Start, stop and restore

1. Back up application SQLite using SQLite's backup API or `sqlite3 ... '.backup ...'`, plus deliberately retained audio. Avoid copying only the main sqlite file while WAL is active.
2. Stop admission before maintenance. Stop active Teams bots and verify they have left. A local application restart alone does not stop a Vexa-owned browser bot.
3. Restore the database, connector journal and retained audio together into an isolated stopped instance; preserve file ownership and restrictive permissions. Do not restore stale active jobs into a live environment without reconciling external Vexa state.
4. On restart, local transcribing jobs become queued. Missing audio fails safely. Unknown join outcome becomes `needs_review`; persisted handles resume exact-session polling.
5. Temporary normalized files from process crashes must be inspected and deleted separately when no work is running.

## Retention

`python scripts/prune_data.py` is a dry-run. `--apply` permanently deletes matching old terminal local jobs and their local retained audio. Active/ambiguous states are excluded. The command is deliberately not scheduled by default. Approve and schedule any production purge under your data-retention policy. Keep an independent Vexa/PostgreSQL/Redis/backups lifecycle; test whether downstream replicas and backups still hold copies.

## Capacity

The included real CPU sample establishes only that the selected model runs. It does not establish a live latency SLO, GPU throughput, production concurrency or quality on Kazakh/English corporate meetings. Track inference queue age, processing duration/audio duration, audio gaps, ASR errors, failed joins, active browsers, RSS/VRAM and free disk. Vexa browser resources are additional to model resources.

If inference falls behind, stop admitting new meetings and keep users informed. Vexa chunk retries/completeness under sustained overload need pilot tests; the service does not promise lossless live capture during outages. Returning 429/503 is explicit failure, not an external fallback.

## Upgrade policy

Do not replace pinned Vexa/GigaAM revisions without reviewing privacy patches, wire contracts, model checksums, licenses and regression tests. Browser selectors may change independently. Resolve policy/access blocks with the organizer/admin/platform instead of bypassing them. Re-test actual joins and stop/denial behavior before upgrading a corporate deployment.
