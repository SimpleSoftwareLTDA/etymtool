# EtymTool

Word etymology lookup tool with SQLite cache that grows organically with demand.

Unlock the hidden layers of language by reading text alongside its etymology. Tracing words back to their roots offers three powerful benefits:

- **Deep Comprehension:** Grasp the true nuances of a text; understanding a word's root meaning often clarifies its usage far better than a modern dictionary definition.
- **Expanded Historical Perspective:** See the evolution of ideas; every word carries the DNA of the culture and history that created it.
- **Critical Textual Analysis:** Decode the author's worldview. Analyzing word origins helps reveal hidden biases, cultural framing, and even logical fallacies rooted in ancient assumptions.

## Architecture

- **Frontend:** Static HTML/JS with CSS tooltips
- **Backend:** FastAPI (Python 3.12) on port 8765
- **Database:** SQLite (`~/.etymtool/etymologies.db`)
- **Data Source:** yosevu/etymonline dataset (46K entries, MIT license)

## How It Works

1. User types/pastes English text
2. Backend tokenizes and lemmatizes each word
3. Checks SQLite cache for etymology
4. Returns word-by-word with optional tooltip data
5. Frontend highlights words with etymology; hover reveals the definition
6. Missing words can be added via scraping (placeholder for future)

## Quick Start

```bash
cd /home/ubuntu/etymtool
python3.12 -m uvicorn server:app --host 0.0.0.0 --port 8765
```

Open `http://localhost:8765` in browser.

## API Endpoints

- `GET /` — Frontend
- `GET /api/etymology/{word}` — Single word lookup
- `POST /api/process-text` — Process full text (body: `{"text": "..."}`)
- `GET /api/stats` — Cache statistics
- `POST /api/import-dataset` — Import the offline etymonline JSON
- `GET /api/health` — Health check

## File Structure

```
etymtool/
├── server.py              # FastAPI backend + lemmatizer + DB
├── static/
│   └── index.html         # Frontend with tooltips
├── README.md
└── ~/.etymtool/
    ├── etymologies.db     # SQLite cache
    └── etymonline_offline.json  # 46K etymology entries
```

## Coverage

- ~38K unique English words with etymology
- Lemmatizer handles: irregular verbs, plurals, gerunds, past participles
- ~77% coverage on content words for typical English prose
- Stop words (the, and, of, etc.) intentionally excluded
