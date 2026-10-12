# Dashboard verification

The dashboard is framework-free HTML, CSS and JavaScript. Node packages are used only for development checks; the deployed FastAPI application does not need Node or npm.

## Reproduce the DOM smoke test

With Node 18 or later:

```sh
npm ci --ignore-scripts
npm test
```

`tests/ui_smoke.cjs` loads the actual `app/static/index.html` and `app/static/app.js` in jsdom 26.1.0. Its API responses are deterministic contract stubs, not a live backend or ASR model. The test does not write meeting fixtures into the application database. No sample transcript is shipped in the dashboard.

The test checks 21 interaction scenarios:

1. Valid login and rejected token
2. Access dialog dismissal and reopening
3. Session-only token storage and no localStorage token
4. Disabled Teams feature gate
5. Keyboard navigation between source tabs
6. Explicit recording/processing consent
7. Multipart upload fields
8. Duplicate-submit prevention
9. Source-specific queued statuses
10. Stop and retry requests
11. Meeting selection and history search
12. User-controlled text remains inert, including HTML-like content
13. Preliminary segment labeling
14. Unicode transcript editing
15. Version-conflict errors preserve the unsaved draft
16. Cancelling navigation keeps unsaved edits
17. Clipboard text
18. Authenticated TXT, Markdown, DOCX and PDF download requests
19. A slower previous selection cannot overwrite newer navigation
20. Teams creation payload
21. Logout clears the token and visible meeting content

The test also asserts that tokens are never included in API URLs and captures jsdom errors. Run backend tests separately to validate the real endpoint behavior.

## Export tests

```sh
.venv/bin/python -m pytest tests/test_exports.py -q
```

Tests cover UTF-8 Kazakh/Russian/English text, timestamp metadata, safe filenames, Markdown escaping, DOCX document XML, PDF signature and extracted Unicode text, empty transcripts, preliminary segments, invalid formats and control characters. PDF extraction checks require the development dependency `pypdf`.

## Verification boundaries

- DOM smoke: passed with contract-stubbed responses and zero jsdom errors.
- Python export tests: passed, including Kazakh-specific glyph extraction from generated PDFs.
- JavaScript syntax, Python lint and HTML ID/label references: checked.
- Browser visual verification: **not completed** in the execution environment. Isolated Chromium aborted before opening a page with `socket() failed: Operation not permitted` in `process_singleton_posix.cc`. A separate approved execution attempt had the same runtime restriction.
- Real microphone/Teams meetings, ASR model performance, screen-reader behavior and a full keyboard/browser matrix are not established by these tests.

Before deployment, check the actual app in Chromium/Firefox and mobile browsers at 390, 768, 1024 and 1440 px widths, including long titles, a long transcript, dialog focus, uploads, draft conflict handling, completed/failed/active states, zoom, and all downloads. Use an authorized test meeting and explicit consent for any Teams recording.
