# Security and local-use boundary

This is a local document extraction program. The UI binds to `127.0.0.1`, checks request host and same-origin browser metadata, and requires a per-process token for writes. Keep it on the local machine; it is not a hosted multi-user service.

Uploaded content is untrusted input. Source HTML, SVG, code and Office documents are downloaded as attachments. The reading preview uses an allowlist and a Content Security Policy. The program does not execute local HTML scripts, import browser cookies, run document macros, recalculate spreadsheet formulas or unlock encrypted documents. Dynamic public URLs deliberately run page JavaScript in a clean browser; only public GET/HEAD traffic is allowed by the application and downloads/service workers/WebSockets are blocked. URL checks are application checks, not an operating-system network sandbox. Use an isolated machine/container for hostile files or sites.

Paths, hashes, extraction limits, skipped content and tool failures are recorded. OCR may misread characters. The manifest and checks distinguish storage integrity from semantic/visual correctness. Original source documents and extraction outputs may contain sensitive content: do not commit `outputs/`, `.engine.json`, credentials or private documents.

The sharing exporter uses a file allowlist, rejects local-path/credential patterns in text, and excludes outputs, caches, virtual environments, local skill bindings and Git credentials. The checks do not constitute a guarantee that arbitrary user documents are safe to publish.

For a suspected vulnerability, contact the repository owner privately through an available GitHub security advisory channel. Do not put credentials, private documents or exploitable personal data in a public issue. No remote repository or reporting channel is assumed to exist before publication.
