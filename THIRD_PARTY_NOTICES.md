# Third-party notices

Source Preserve MD / 원문결 is distributed under AGPL-3.0-only; see [LICENSE](LICENSE). Copyright 2026 Source Preserve MD contributors. This license covers this project's original code and skill instructions. Original documents supplied by users retain their own rights.

Dependencies are installed separately; they are not relicensed by this repository. The pinned dependency graph is in `uv.lock`, and installed distributions include their own license metadata and notices.

- PyMuPDF: Artifex and other contributors; [AGPL v3 or commercial](https://pymupdf.readthedocs.io/en/latest/about.html). This distribution uses the open-source dependency.
- pyhwp 0.1b15: mete0r and contributors; [AGPL v3 or later](https://github.com/mete0r/pyhwp/blob/83239f0d3bdf438b2c9f7dcff455a6e841154a39/COPYING).
- Beautiful Soup, charset-normalizer, defusedxml, httpx, lxml, markdown-it-py, olefile, openpyxl, python-docx, python-pptx, PyYAML, six and striprtf retain their respective upstream notices and licenses.
- Flask/Werkzeug: Pallets and contributors, BSD-3-Clause. Pillow: upstream MIT-CMU license. Playwright: Microsoft and contributors, Apache-2.0. Transitive dependencies retain their respective upstream licenses.
- Apple Vision and Swift tools, Chrome/Chromium and LibreOffice are optional external runtimes. Their binaries are not included. Installing this repository does not install or license those runtimes on the user's behalf.

## Included test documents

`examples/legacy-inputs/{table,footnote-endnote,password-12345}.hwp` are unchanged pyhwp test fixtures from commit `83239f0d3bdf438b2c9f7dcff455a6e841154a39`. Copyright their upstream authors; the upstream repository is AGPL v3 or later. Exact source URLs and SHA-256 values are recorded in `examples/legacy-inputs/sources.json`. The AGPL v3 text is included in this repository's LICENSE.

The `examples/quality-inputs/sources.json` catalog records external public documents for optional quality checks. Most documents are downloaded only on explicit request and are excluded from the sharing export. The included `python-controlflow.html` is an unchanged snapshot of Python documentation, retained because the live page changes; its original URL and SHA-256 are in the catalog. Copyright Python Software Foundation and contributors; the license is included as `examples/quality-inputs/PYTHON_LICENSE.txt`. python-docx/python-pptx use MIT licenses and pandas uses BSD-3-Clause; W3C materials have their own upstream terms. Consult the catalog and the original repositories when redistributing downloaded documents. Generated verification outputs and personal source documents are excluded from this distribution.
