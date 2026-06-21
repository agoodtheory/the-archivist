# 👁️ The Archivist

**A desktop ETL pipeline for normalizing heterogeneous datasets into a serverless RAG architecture.**

> *Built to feed [The Compendium](https://agoodtheory.dev) — a private, AI-powered research archive for cataloging and cross-referencing reported paranormal phenomena (Bigfoot sightings, UFO/UAP reports, Missing 411 cases, and more) into a unified, queryable schema.*

---

## What It Does

The Archivist takes messy, inconsistently-formatted source data — CSV, XLSX, or PDF — and turns it into clean, schema-conformant records ready for ingestion into a vector-search-backed research archive.

Point it at a file and it will:
- **Auto-detect column mappings** using fuzzy string matching against a configurable field-synonym dictionary (e.g. a column named `"sighting_report"` or `"what_happened"` both map to `summary`)
- **Show a live JSON preview** of the mapped entry before you commit to anything, with a row cycler to spot-check the mapping across the dataset
- **Estimate cost up front** — token counts and projected OpenAI spend (embeddings + summarization) before a single API call is made
- **Summarize long-form text** via GPT-4o-mini when a field exceeds a configurable length, keeping embedding cost and retrieval quality both in check
- **Check for duplicates** against a DynamoDB Global Secondary Index before writing anything
- **Deliver in one pass** to S3 (raw text), DynamoDB (structured metadata), and Pinecone (vector embeddings) — or export to local JSON instead, for offline review or batch runs

Packaged as a portable Windows executable via PyInstaller — no Python install required to run it.

---

## How It Works

### Architecture

```
File input (CSV / XLSX / PDF)
      ↓
detect_column_mapping()      — fuzzy match (thefuzz) against FIELD_SYNONYMS
      ↓
Manual review + correction UI — live JSON preview, per-row cycling
      ↓
estimate_costs()             — token/cost projection before any API call
      ↓
map_rows()                   — builds entry dicts conforming to BASE_ENTRY schema
      ↓
build_embed_text()           — compact embed string; OpenAI summarization if summary > threshold
      ↓
process_and_deliver()        — one-pass fork:
    ├── write_entry_local()  — JSON file per entry, timestamped output folder
    └── ingest_to_compendium()
            ├── entry_exists()           — GSI duplicate check
            ├── s3_client.put_object()   — raw text → S3
            ├── table.put_item()         — structured record → DynamoDB
            └── pinecone_index.upsert()  — embedding → Pinecone
```

### Entry Schema

Every record conforms to a fixed schema regardless of source format:

```python
BASE_ENTRY = {
    "id": None,               # deterministic ("{source}_{number}") or UUID
    "title": None,
    "category": None,         # user-selected, configurable list
    "type": None,              # "account" for all current ingestion paths
    "source": None,            # origin filename
    "date_of_event": None,     # ISO date if parseable, raw string otherwise
    "date_added": None,
    "location": {
        "description": None,
        "city": None,
        "state": None,         # normalized to two-letter abbreviation
        "country": "USA",
        "coordinates": None    # "lat,lon" string
    },
    "witness_count": None,
    "physical_evidence": None,
    "summary": None,           # primary narrative; summarized if > threshold
    "tags": [],
    "raw_text": None,          # full concatenated source text
    "embed_text": None,        # compact string sent to the embedding model
    "notes": None
}
```

### Fuzzy Column Detection

Rather than requiring exact column names, incoming columns are matched against a synonym dictionary using string similarity scoring:

```python
FIELD_SYNONYMS = {
    "summary":       ["observed", "description", "report", "sighting", "details", ...],
    "title":         ["title", "name", "report_title", "heading", "subject"],
    "state":         ["state", "st", "state_name", "location_state", "province", ...],
    "date_of_event": ["date", "date_of_event", "event_date", "year", "occurred", ...],
    # ...
}
```

A column like `"WHAT_WAS_OBSERVED"` matches `summary` without any manual configuration. Low-confidence matches are flagged for manual review rather than silently guessed.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Desktop UI | Tkinter (`ttk`, native Windows theming) |
| Fuzzy matching | `thefuzz` |
| Tabular parsing | `pandas`, `openpyxl` |
| PDF parsing | `pdfplumber` |
| Embeddings | OpenAI `text-embedding-3-small` |
| Summarization | OpenAI `gpt-4o-mini` (conditional, length-gated) |
| Object storage | AWS S3 (`boto3`) |
| Structured storage | AWS DynamoDB (`boto3`) |
| Vector storage | Pinecone |
| Packaging | PyInstaller (`--onefile --windowed`) |

---

## Repository Structure

```
the-archivist/
├── The_Archivist.py     # Full application source
├── requirements.txt
├── LICENSE
└── README.md
```

`config.json` (local settings — env file path, output directory, category list) and `.env` (API keys) are created locally on first run and are never committed — see `.gitignore`.

---

## Running Locally

**Requirements:** Python 3.10+, pip, a Windows environment (Tkinter theming uses `xpnative`; will run on other platforms with minor theme adjustments)

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python The_Archivist.py
```

On first launch, go to **Settings → Preferences** and point the app at:
- An `.env` file containing your API credentials (see below)
- An output folder for local JSON exports

### Required `.env` keys

```
OPENAI_API_KEY=sk-...
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
PINECONE_API_KEY=...
```

AWS/Pinecone ingestion is optional — the app runs fine in local-export-only mode without cloud credentials configured, since each row's destination (cloud, local, or both) is chosen per ingestion run.

### Building the executable

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --icon="all_seeing_eye.ico" --add-data "all_seeing_eye.ico;." The_Archivist.py
```

Output: `dist/The_Archivist.exe`.

---

## Known Limitations & Design Decisions

- **Windows-first:** Native theming (`ttk.Style().theme_use("xpnative")`) and the packaged `.exe` target Windows specifically. The Python source runs cross-platform with a fallback theme.
- **No automated mapping memory (yet):** Column mappings are saved per-dataset after a session but aren't auto-loaded on re-runs of the same file — see roadmap.
- **Cloud ingestion is tied to a specific schema:** `S3_BUCKET`, `DYNAMO_TABLE`, and `PINECONE_INDEX_NAME` are configured for a specific downstream system (The Compendium) rather than being fully generic — this is a portfolio piece demonstrating the ingestion pattern, not a general-purpose tool, though the fuzzy-matching and schema-mapping logic generalizes cleanly to other targets.

## Roadmap

- Smart mapping cache — load previously-saved `{filename}_mapping.json` automatically on re-runs
- Generalize cloud destination config so the tool can target arbitrary DynamoDB/S3/Pinecone resources without code changes

---

## Future Expansion: Cloud-Native Ingestion

The Archivist currently runs as a desktop app — a deliberate choice, since the column-mapping and review step benefits from a human looking at a live JSON preview before anything gets written. That works well for normal-sized datasets, but it doesn't scale to very large ones: a multi-hundred-thousand-row dataset run entirely on local hardware, rate-limited to stay under API limits, can take days of unattended desktop runtime to fully process.

**The plan: a browser-based companion app for large batch runs, paired with the same EC2 infrastructure already planned for [The Compendium's vector search migration](https://github.com/agoodtheory/compendium#future-expansion-self-hosted-vector-search).**

The rough shape of it:
- A lightweight web frontend handles file upload (to S3) and the same column-mapping review UI the desktop app already has — that human-in-the-loop step isn't going away, it's just moving to a browser
- Once mapping is confirmed, the actual processing — fuzzy matching, cost estimation, summarization, duplicate checking, and delivery to DynamoDB/S3/Pinecone — runs headlessly on an EC2 instance instead of the user's local machine
- That instance can reasonably be the same one hosting Qdrant once that migration happens (see the Compendium repo), since both workloads are intermittent rather than constant — started on demand, stopped when idle, same start/stop cost pattern
- The desktop app stays relevant for smaller, ad-hoc additions where local processing is fast enough that cloud round-trip latency isn't worth it; the web app targets the large-batch case specifically, rather than replacing the desktop tool outright

This turns a "leave a desktop app running for two days" workflow into "upload a file and check back later" — without giving up the manual review step that keeps bad column mappings from silently corrupting the schema.

---

## Credits

App icon: <a href="https://www.flaticon.com/free-icons/all-seeing-eye" title="all-seeing eye icons">All-seeing eye icons created by Freepik - Flaticon</a>

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built for cataloging the unexplained, one normalized row at a time.*
