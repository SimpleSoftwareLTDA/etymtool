# EtymTool — Agent Guidelines

Instructions for AI agents working on this codebase.

## Project Overview

EtymTool provides word etymology lookups via a FastAPI backend. It uses an offline dataset (46K entries from etymonline), a SQLite cache that grows organically, and a Firecrawl fallback for words not in the offline dataset.

```
etymtool/
├── server.py              # FastAPI backend + lemmatizer + DB + scraper
├── static/
│   └── index.html         # Frontend with CSS tooltips
├── AGENTS.md
├── README.md
└── .gitignore

~/.etymtool/
├── etymologies.db         # SQLite cache (gitignored)
└── etymonline_offline.json  # 46K base dataset (gitignored)
```

## Running the Server

```bash
export FIRECRAWL_API_KEY=fc-YOUR_KEY
python3.12 -m uvicorn server:app --host 0.0.0.0 --port 8765
```

## Python Best Practices

### Type Hints

Use type hints on all function signatures and return types. Include `dict | None` instead of `Optional[dict]` (Python 3.10+ syntax).

```python
def fetch_etymology_for_word(word: str, allow_scrape: bool = True) -> dict | None:
```

### Docstrings

Use triple-quoted docstrings for all public functions. Keep them concise and focused on the contract, not the implementation.

```python
def lemmatize_word(word: str) -> str:
    """Reduce a word to its dictionary form. Handles irregular verbs, plurals, and common suffixes."""
```

### Error Handling

- Never let external API calls crash the server. Wrap `subprocess`, `requests`, and `sqlite3` calls in `try/except`.
- Return `None` or structured error dicts from API endpoints; let the HTTP layer handle status codes.
- Use `logging.warning` for recoverable failures; never `print()` for error reporting.

### Database

- Always use parameterized queries; never string interpolation.
- Use WAL mode for concurrent reads.
- Commit in batches for bulk inserts (every 5000 rows).
- Use `INSERT OR REPLACE` for idempotent writes.

### Subprocess Calls

- Always set `timeout` on `subprocess.run`.
- Capture both stdout and stderr.
- Pass explicit environment dicts; never rely on inherited env for API keys.
- Validate return codes before processing output.

### Code Organization

- Keep the single-file structure for now; split into modules if `server.py` exceeds 800 lines.
- Group constants at the top: config, DB paths, rate limits, stop words.
- Separate concerns: DB functions, lemmatizer, scraper, FastAPI routes.

## Linguistics Best Practices

### Lemmatization

The lemmatizer is heuristic, not perfect. Know its blind spots:

- **Irregular forms** must be mapped explicitly in `IRREGULAR_VERBS`. Common ones: went->go, saw->see, felt->feel.
- **Plural handling** uses suffix rules; false positives happen (e.g., "news" → "new"). Prefer checking the base word first before stripping suffixes.
- **Homographs** (words with same spelling, different origins): the offline dataset and etymonline use numbered entries like `wind(n.1)` vs `wind(v.1)`. Always include the POS tag in the tooltip.
- **Compound words**: "long-winded" should map to "wind" as a component, but the current tokenizer splits on hyphens. Handle compounds as a future improvement.

### Etymology Data Quality

- **Source attribution**: always track the `source` field in the DB (`etymonline_offline`, `firecrawl`, `scraper`). This lets you trace data quality issues back to the source.
- **Markdown parsing**: Firecrawl returns etymonline content as markdown. Strip ad-related text (`Advertisement`, `Want to remove ads?`), image links, and cross-reference blocks before displaying.
- **Cross-references**: etymonline pages link related words (e.g., `See also thought`). These are not etymologies; exclude them from the tooltip content.
- **Dates**: etymology dates like `late 13c.` or `c. 1200` indicate first attestation, not origin. When displaying, clarify this distinction. A word can be attested in 1200 but borrowed from Latin centuries earlier.

### Language Coverage

- The offline dataset covers **English only** (etymonline scope).
- Latin roots (PIE, Proto-Germanic, Old English) are included in etymology text; do not treat them as separate language lookups.
- Stop words and function words (the, a, of, in) are intentionally excluded from lookups; they rarely have interesting etymologies and add noise.

### Text Tokenization

- The regex `[a-zA-Z][a-zA-Z'-]*|[^\w\s]+|\s+` handles English text. It will split hyphenated compounds (`long-winded` → `long`, `winded`). This is acceptable for the MVP.
- Punctuation and whitespace pass through without lookup.
- Non-ASCII words fall through silently; future versions should detect language and route appropriately.

## Development Workflow

1. Test all changes locally before committing.
2. Run the full text processing endpoint with a paragraph of 50+ words to verify coverage.
3. Verify that the frontend renders tooltips correctly (hover behavior, edge positioning).
4. Check that the SQLite cache does not grow unbounded; run `ANALYZE` periodically.
5. Commit with descriptive messages: `fix: handle irregular verb 'brought' in lemmatizer` not `update`.
