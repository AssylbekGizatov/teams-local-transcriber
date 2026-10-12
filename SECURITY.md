# Security and deployment boundary

This is a single-operator, single-node pilot. Do not expose it directly to the public Internet or treat it as certified Teams recording software.

## Launcher and provisioning boundary

The Windows easy-start scripts are convenience entry points, not permission to install or run software prohibited by the organization. They do not install Docker, elevate to administrator, weaken PowerShell execution policy or bypass corporate controls. If the scripts or Docker are blocked, stop and ask IT for an approved deployment or service URL.

Everyday launch uses an already provisioned local image and model. IT preparation is a distinct, explicitly approved build/download step. Review the source, outbound dependency/model sources, licenses and final image before approval; pinned model hashes do not establish that an entire OS image or dependency chain is safe. An offline image archive and model directory must be supplied and verified through a trusted IT process. The source ZIP alone is not an offline runtime.

Keep `.env`, local tokens, recordings, transcripts, Docker volumes and exported files out of source archives and public repositories. Do not send credentials in a URL or paste diagnostic output containing secrets. A localhost-only listener reduces network exposure but does not protect against other authorized processes or users on the host. File permissions and host/device controls remain IT responsibilities.

## Trust boundaries

- The browser operator token can read/edit/export every meeting on this instance. There are no per-user ACLs, SSO, independent organizations, tenant isolation or user audit attribution. Put an approved identity-aware TLS proxy and VPN/network policy in front, or run one isolated instance per authorized group.
- The separate inference token can submit audio only. It cannot list or export stored meetings. Tokens must be distinct, >=32 characters, and are compared in constant time. Request authentication happens before multipart parsing.
- Meeting URLs are accepted only for known direct HTTPS Teams shapes. Exact meeting IDs must be allowlisted. An unsigned tenant hint in a URL never grants authority. Secrets and meeting URLs are not placed in frontend URLs or access logs.
- Vexa owns browser capture. Its Docker runtime needs a socket to launch bots: this is effectively host-root capability. Use a dedicated disposable VM with no unrelated workloads or credentials. Do not mount this socket in the transcription application.
- Model files are provisioned explicitly and mounted read-only. GigaAM code and artifact SHA256 values are pinned, no HF remote Python is executed, and PyTorch loads weights with weights_only=True. No external fallback exists.

## Upstream privacy hardening

Only use the vendor script's exact Vexa revision plus its mandatory source patch. The patch removes/blocks capture-signal sidecars and raw recording requests, disables caption ingestion/logging, removes stealth/fingerprint manipulation, prevents identity transcription settings from overriding the local endpoint, and limits the inference origin to the dedicated internal ASR service. See deployment tests and TEAMS_CONNECTOR.md for exact checks. An upstream default image is not equivalent.

These code controls do not prove acceptable platform use, participant consent or tenant authorization. Browser compatibility and platform policy may change. Do not work around CAPTCHA, login challenges, organizer rejection, lobby policy or recording restrictions.

## Retention and logs

Raw upload retention is off by default. Inference request WAVs and normal processing temp directories are deleted when calls finish. A process crash can leave abandoned files; inspect/remove only confirmed orphaned files during maintenance. Do not run broad cleanup while jobs are active. Disks, snapshots and backups require separate encryption/deletion policy.

Transcripts remain in the application and in Vexa's database/Redis. The local pruning script only deletes selected terminal application jobs, not Vexa or backups. The browser may retain downloaded exports. Browser/API operators may copy text. Test end-to-end erasure before processing corporate data.

Audit rows record events/segment IDs, not transcript text. App exception messages are sanitized. Some upstream logs may still carry meeting metadata; treat all runtime logs as potentially confidential, bound retention, and inspect the final container logs in your pilot. Do not claim zero metadata logging.

## Resource limits and failure modes

Upload bytes, audio duration, decode timeout, pending jobs and inference waiters are bounded. FFmpeg accepts only local files with a limited demuxer/protocol allowlist and runs without a shell. Still isolate audio processing: media codecs are complex parsers, not a sandbox. CPU/memory/PID caps are set in Compose; exact concurrency is unbenchmarked.

A single dispatcher owns SQLite jobs. Multiple Uvicorn workers or multiple application replicas are unsupported. No auto-rejoin follows an uncertain external operation. Verify the actual Teams participant and Vexa session when a job needs review. Liveness is not readiness; a configured model path does not guarantee a successful model load.

## Before an authorized pilot

1. Approve platform method and participant notice; record bounded meeting IDs.
2. Verify no external STT/LLM endpoints through effective runtime config and egress observation.
3. Verify the bot leaves after stop, organizer rejection, max-duration and server restart.
4. Test recovery, local-ASR outage/backpressure and transcript completeness, not only successful join.
5. Configure TLS, disk encryption, secrets storage, backups, expiration and access review.
6. Scan built image/dependency SBOM and pin accepted base-image digests.
7. Benchmark KK/RU/EN and mixed-language speech on authorized evaluation audio. Do not use ASR text as an unverified legal/medical/financial record.

Report vulnerabilities privately to the repository maintainer; do not attach tokens, real audio, transcripts or usable meeting links to public issues.
