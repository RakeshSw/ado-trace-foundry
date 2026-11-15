# ADOTraceFoundry test plan

1. **Offline smoke test:** run `python -m adotracefoundry demo --input samples/workitems.json --attachments-dir samples/attachments --output output/demo` and inspect manifest.
2. **Graph integrity:** ensure 5 unique IDs, 4 deduplicated parent-child links and 2 paths: 101→201→301 and 101→202→302.
3. **Attachment:** check `output/demo/knowledge/201.md` includes synthetic checkout notes and local manifest has `sha256`.
4. **Missing relations:** add a reference to nonexistent ID and inspect `unresolved-links.json`.
5. **Revisions:** provide two versions of an ID, confirm highest `rev` is retained.
6. **Live integration (optional):** create synthetic items in an authorized disposable ADO project and run WIQL read-only export. Confirm no writes or attachments are exposed in GitHub.
7. **Documents (optional):** install extras and run with a permitted sample PDF/DOCX/PPTX/XLSX; compare extracted text with source.
8. **OCR (optional):** requires system Tesseract and explicit `--ocr`; scanned PDF OCR is not yet implemented.

## Release gate

GitHub Actions green, tests pass locally, sample contains no real data or secrets, documented limitations accurate, and no claim of live validation unless separately proven.
