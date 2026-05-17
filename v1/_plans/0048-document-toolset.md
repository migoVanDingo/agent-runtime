# 0048 — Document Toolset

## Scope

A `document` toolset for extracting text and metadata from binary document
formats — primarily PDF and DOCX. Fills the gap between `file_io` (which reads
plain text files) and the analysis toolset (which handles binaries/executables).
Documents like papers, reports, contracts, and presentations are binary files
that can't be read with `read_file`.

---

## Tools

### `read_pdf`

Extract text from a PDF file.

**Inputs:**
- `path` (required) — path to the PDF file
- `pages` — page range: `"1"`, `"1-5"`, `"3,7,12"` (1-indexed, default: all)
- `artifact_key` — optional key to store extracted text as an artifact

**Output:**
```
PDF: /path/to/file.pdf
Pages: 1-24 (extracting: 1-5)

[Page 1]
<text content>

[Page 2]
<text content>
...
```
Stores as artifact if `artifact_key` is provided. Caps inline output at 40 kB
with a note to use `get_artifact` or `read_file_lines` for the rest.

**Dependency:** `pypdf>=4.0.0` (pure Python, no system deps)

---

### `read_docx`

Extract text from a Microsoft Word .docx file.

**Inputs:**
- `path` (required) — path to the .docx file
- `include_headers_footers` — boolean, default false
- `artifact_key` — optional artifact key

**Output:**
```
DOCX: /path/to/file.docx
Paragraphs: 142  |  Tables: 3

<full document text>
```
Preserves paragraph breaks. Tables are rendered as pipe-delimited text.
Caps inline output at 40 kB.

**Dependency:** `python-docx>=1.0.0`

---

### `document_info`

Metadata and statistics for PDF or DOCX files without extracting full text.

**Inputs:**
- `path` (required) — path to PDF or DOCX

**Output (PDF):**
```
Format: PDF
File: report.pdf (2.4 MB)
Pages: 24
Title: Annual Report 2024
Author: Jane Smith
Creator: Microsoft Word
Created: 2024-01-15
Modified: 2024-03-20
Encrypted: No
```

**Output (DOCX):**
```
Format: DOCX
File: report.docx (1.1 MB)
Paragraphs: 142
Tables: 3
Title: Annual Report 2024
Author: Jane Smith
Last Modified: 2024-03-20
Words (estimated): ~4,200
```

**Guard:** ALLOW — metadata only, no content extraction.

---

### `read_epub`

Extract text from an EPUB e-book.

**Inputs:**
- `path` (required) — path to .epub
- `chapters` — optional comma-separated chapter indices (1-indexed)
- `artifact_key` — optional

**Output:** Chapter-by-chapter text extraction. Caps at 40 kB inline.

**Dependency:** `ebooklib>=0.18` (lazy import, optional)

---

## Routing Rules

```python
DOCUMENT = Toolset(
    name="document",
    planning_note=(
        "Use read_pdf to extract text from PDF files. "
        "Use read_docx for Word documents. "
        "Use read_epub for ebooks. "
        "Use document_info for quick metadata without full text extraction. "
        "After extracting to an artifact, use get_artifact or read_file_lines to read chunks."
    ),
    rules=[
        has_extension(".pdf", ".docx", ".doc", ".epub"),
        any_keyword(
            "pdf", "docx", "word document", "epub", "ebook",
            "read pdf", "extract pdf", "read document", "document text",
            "pages", "read ebook",
        ),
    ],
)
```

---

## ActionType

Adds `DOCUMENT = "document"` to `ActionType` enum and `PLAN_JSON_SCHEMA`.

---

## Guard

All document tools are read-only — ALLOW. No sensitive paths or network access.

---

## Dependencies

| Dependency | Phase | Already present? |
|-----------|-------|----------------|
| `pypdf>=4.0.0` | PDF | No — add to requirements.txt |
| `python-docx>=1.0.0` | DOCX | No — add to requirements.txt |
| `ebooklib>=0.18` | EPUB | No — lazy import, add to requirements.txt |

All three are pure Python. `pypdf` was previously `PyPDF2`, now maintained as
`pypdf`. `python-docx` requires `lxml` as a transitive dependency.

---

## Error Handling

- File does not exist → `Error: file not found: <path>`
- Wrong format (e.g. `read_pdf` on a DOCX) → `Error: <path> does not appear to be a valid PDF`
- Encrypted PDF without password → `Error: PDF is encrypted and requires a password`
- Missing dependency → `Error: pypdf is not installed. Run: pip install pypdf`

---

## Files

| File | Change |
|------|--------|
| `src/tools/implementations/document/__init__.py` | New |
| `src/tools/implementations/document/read_pdf.py` | New |
| `src/tools/implementations/document/read_docx.py` | New |
| `src/tools/implementations/document/document_info.py` | New |
| `src/tools/implementations/document/read_epub.py` | New |
| `src/tools/toolsets.py` | Add DOCUMENT toolset + imports |
| `src/planning/schema.py` | Add `ActionType.DOCUMENT` |
| `config.yml` | Add `document` to `toolset_descriptions` |
| `requirements.txt` | Add pypdf, python-docx, ebooklib |
