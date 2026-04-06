"""
EtymTool — Backend FastAPI para consulta de etimologias.

Fonte primaria: dataset yosevu/etymonline (46K entradas, JSON)
Fonte secundaria: scrap do etymonline.com (requer browser JS rendering, ainda nao implementado)
Cache: SQLite local, cresce organico com cada consulta.
"""

import json
import os
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("ETYMDB", Path.home() / ".etymtool" / "etymologies.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

DATASET_PATHS = [
    Path.home() / ".etymtool" / "etymonline_offline.json",
    "/tmp/etymonline.json",
]

ETYMONLINE_BASE = "https://www.etymonline.com/word/"
REQUEST_TIMEOUT = 10
RATE_LIMIT_DELAY = 0.5

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etymologies (
            word TEXT PRIMARY KEY,
            etymology TEXT NOT NULL,
            pos TEXT,
            origin_year INTEGER,
            source TEXT DEFAULT 'cache',
            fetched_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etym_word ON etymologies(word)")
    conn.commit()
    conn.close()


def lookup_cached(word: str) -> dict | None:
    """Look up a word in the SQLite cache."""
    conn = get_db()
    row = conn.execute(
        "SELECT word, etymology, pos, origin_year, source FROM etymologies WHERE LOWER(word) = ?",
        (word.lower(),),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def save_etymology(word: str, etymology: str, pos: str = None, origin_year: int = None,
                   source: str = 'scraper'):
    """Insert or replace an etymology in the cache."""
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO etymologies (word, etymology, pos, origin_year, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))""",
        (word.lower(), etymology, pos, origin_year, source),
    )
    conn.commit()
    conn.close()


def cache_stats() -> dict:
    """Return count of cached entries and oldest/newest date."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as cnt FROM etymologies").fetchone()["cnt"]
    oldest = conn.execute(
        "SELECT word, datetime(fetched_at, 'unixepoch') as dt FROM etymologies ORDER BY fetched_at LIMIT 1"
    ).fetchone()
    sources = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM etymologies GROUP BY source"
    ).fetchall()
    sample = conn.execute(
        "SELECT word FROM etymologies ORDER BY RANDOM() LIMIT 10"
    ).fetchall()
    conn.close()
    return {
        "count": count,
        "oldest": dict(oldest) if oldest else None,
        "sources": {r["source"]: r["cnt"] for r in sources},
        "sample": [r["word"] for r in sample],
    }


# ---------------------------------------------------------------------------
# Offline dataset loader
# ---------------------------------------------------------------------------

_OFFLINE_LOADED = False

def ensure_offline_dataset_loaded() -> bool:
    """Load the offline dataset into SQLite if not already done."""
    global _OFFLINE_LOADED
    if _OFFLINE_LOADED:
        return True

    # Check if we already have entries from the offline source
    conn = get_db()
    existing = conn.execute(
        "SELECT COUNT(*) as cnt FROM etymologies WHERE source = 'etymonline_offline'"
    ).fetchone()["cnt"]
    conn.close()
    if existing > 10000:
        _OFFLINE_LOADED = True
        return True

    # Find and load the dataset
    data = None
    for path in DATASET_PATHS:
        if Path(path).exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                break
            except (json.JSONDecodeError, OSError):
                continue

    if not data:
        return False

    conn = get_db()
    count = 0
    for entry in data:
        word = entry.get("word", "").lower().strip()
        etymology = entry.get("etymology", "").strip()
        pos = entry.get("pos")
        years = entry.get("years", [])
        origin_year = years[0] if years else None
        if not word or not etymology:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO etymologies (word, etymology, pos, origin_year, source) "
            "VALUES (?, ?, ?, ?, 'etymonline_offline')",
            (word, etymology, pos, origin_year),
        )
        count += 1
        if count % 5000 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    _OFFLINE_LOADED = True
    return count > 0


# ---------------------------------------------------------------------------
# Lemmatizer
# ---------------------------------------------------------------------------

try:
    import nltk
    nltk.data.find("corpora/wordnet")
except (LookupError, ImportError):
    import nltk
    nltk.download("wordnet", quiet=True)
    nltk.download("omw-1.4", quiet=True)

from nltk.stem import WordNetLemmatizer

_lemmatizer = WordNetLemmatizer()

IRREGULAR_VERBS = {
    "was": "be", "were": "be", "been": "be",
    "ran": "run", "running": "run",
    "went": "go", "gone": "go", "going": "go",
    "saw": "see", "seen": "see", "seeing": "see",
    "took": "take", "taken": "take", "taking": "take",
    "came": "come", "coming": "come",
    "made": "make", "making": "make",
    "knew": "know", "known": "know",
    "thought": "think", "thinking": "think",
    "gave": "give", "given": "give", "giving": "give",
    "found": "find", "finding": "find",
    "told": "tell", "telling": "tell",
    "became": "become", "becoming": "become",
    "left": "leave", "leaving": "leave",
    "felt": "feel", "feeling": "feel",
    "brought": "bring", "bringing": "bring",
    "began": "begin", "begun": "begin", "beginning": "begin",
    "kept": "keep", "keeping": "keep",
    "held": "hold", "holding": "hold",
    "wrote": "write", "written": "write", "writing": "write",
    "stood": "stand", "standing": "stand",
    "heard": "hear", "hearing": "hear",
    "meant": "mean", "meaning": "mean",
    "met": "meet", "meeting": "meet",
    "paid": "pay", "paying": "pay",
    "sat": "sit", "sitting": "sit",
    "spoke": "speak", "spoken": "speak", "speaking": "speak",
    "rose": "rise", "risen": "rise", "rising": "rise",
    "grew": "grow", "grown": "grow", "growing": "grow",
    "fell": "fall", "fallen": "fall", "falling": "fall",
    "drove": "drive", "driven": "drive", "driving": "drive",
    "chose": "choose", "chosen": "choose", "choosing": "choose",
    "drew": "draw", "drawn": "draw", "drawing": "draw",
    "broke": "break", "broken": "break", "breaking": "break",
    "ate": "eat", "eaten": "eat",
    "forgot": "forget", "forgotten": "forget",
    "wore": "wear", "worn": "wear",
    "swore": "swear", "sworn": "swear",
    "stole": "steal", "stolen": "steal",
    "froze": "freeze", "frozen": "freeze",
    "shook": "shake", "shaken": "shake",
    "understood": "understand",
    "withdrew": "withdraw", "withdrawn": "withdraw",
    "bought": "buy",
    "sold": "sell",
    "taught": "teach",
    "caught": "catch",
    "fought": "fight",
    "sought": "seek",
    "built": "build",
    "spent": "spend",
    "lent": "lend",
    "slept": "sleep",
    "lost": "lose",
    "shot": "shoot",
    "tore": "tear", "torn": "tear",
    "hid": "hide", "hidden": "hide",
}

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "don", "now", "and", "but", "or",
    "if", "because", "while", "about", "against", "up", "down", "that",
    "this", "these", "those", "i", "me", "my", "we", "our", "you",
    "your", "he", "him", "his", "she", "her", "it", "its", "they",
    "them", "their", "what", "which", "who", "whom", "am",
}


def _plural_to_singular(noun: str) -> str:
    """Basic noun plural to singular reduction for common patterns."""
    if noun.endswith("ies") and len(noun) > 4:
        return noun[:-3] + "y"
    if noun.endswith("ches") or noun.endswith("shes") or noun.endswith("xes") or noun.endswith("zes"):
        return noun[:-2]
    if noun.endswith("ses") and not noun.endswith("sses"):
        return noun[:-2]
    if noun.endswith("ves") and len(noun) > 4:
        return noun[:-3] + "f"  # wolves -> wolf, but NOT leaves
    if noun.endswith("es") and len(noun) > 3:
        return noun[:-2]
    if noun.endswith("s") and not noun.endswith("ss") and not noun.endswith("us") and len(noun) > 3:
        return noun[:-1]
    return noun


def lemmatize_word(word: str) -> str:
    """Reduce a word to its dictionary form."""
    lower = word.lower()
    if lower in IRREGULAR_VERBS:
        return IRREGULAR_VERBS[lower]

    # Try noun form first: students -> student, woods -> wood
    noun_lemma = _plural_to_singular(lower)
    if noun_lemma != lower:
        return noun_lemma

    # Try verb form
    verb_lemma = _lemmatizer.lemmatize(lower, pos="v")
    if verb_lemma != lower:
        return verb_lemma

    return lower


def _is_word_worth_checking(word: str) -> bool:
    cleaned = re.sub(r"[^a-z\-']", "", word.lower())
    return len(cleaned) >= 3 and cleaned not in STOP_WORDS and cleaned.isascii()


# ---------------------------------------------------------------------------
# Firecrawl fallback
# ---------------------------------------------------------------------------
_FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
_last_firecrawl_call = 0
FIRECRAWL_RATE_LIMIT = 2  # seconds between calls

# Track words that returned 404 so we don't retry them
_UNKNOWN_WORDS: set = set()


def _parse_etymonline_markdown(md: str, word: str) -> dict | None:
    """
    Parse the markdown from Firecrawl scrape of etymonline.
    Expected structure:
    ## word(n./v./adj.)
    etymology text...
    ## Entries linking to _word_
    """
    # Split into sections by ## headings
    sections = re.split(r'\n## ', md)

    if not sections:
        return None

    etymology_text = ""
    pos = None

    for section in sections:
        # Match: "wind(n.1)" or "word (n.)" at start of section line
        m = re.match(r'^' + re.escape(word) + r'\s*\(([^)]+)\)', section, re.IGNORECASE)
        if m:
            pos = m.group(1)
            lines = section.split('\n', 1)
            if len(lines) > 1:
                etym_raw = lines[1].strip()
                etym_raw = re.sub(r'\n## .*', '', etym_raw, flags=re.DOTALL)
                etym_raw = re.sub(r'!\[.*?\]\(.*?\)', '', etym_raw)
                etym_raw = re.sub(r'\[also from.*?\]\(.*?\)', '', etym_raw)
                etym_raw = re.sub(r'\[.*?\]\(.*?\)', r'***', etym_raw)
                etym_raw = re.sub(r'\n{2,}', '\n\n', etym_raw).strip()
                # Strip remaining markdown emphasis markers
                etym_raw = re.sub(r'\*\*(.*?)\*\*', r'\1', etym_raw)
                etym_raw = re.sub(r'\*\*(.*?)\*\*', r'\1', etym_raw)  # nested
                etymology_text = etym_raw[:3000]
                break

    if not etymology_text:
        return None

    # Extract first year mentioned as origin
    years = re.findall(r'\b(\d{3,4})\s*(?:century|c\.|BC|AD|BCE|CE)?', etymology_text)
    origin_year = int(years[0]) if years else None

    return {
        "word": word,
        "etymology": etymology_text,
        "pos": pos,
        "origin_year": origin_year,
    }


def _scrape_etymonline_firecrawl(word: str) -> dict | None:
    """Use Firecrawl CLI to scrape etymonline.com/word/{word}."""
    global _last_firecrawl_call, _UNKNOWN_WORDS

    lower = word.lower()

    # Skip if we already know this word doesn't exist
    if lower in _UNKNOWN_WORDS:
        return None

    # Rate limit
    import time as _time
    now = _time.time()
    elapsed = now - _last_firecrawl_call
    if elapsed < FIRECRAWL_RATE_LIMIT and _last_firecrawl_call > 0:
        _time.sleep(FIRECRAWL_RATE_LIMIT - elapsed)

    url = f"https://www.etymonline.com/word/{lower}"
    cmd = [
        "firecrawl", "scrape", url,
        "--only-main-content",
    ]

    env = os.environ.copy()
    env["FIRECRAWL_API_KEY"] = _FIRECRAWL_API_KEY

    try:
        import subprocess
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env
        )
        _last_firecrawl_call = _time.time()

        if result.returncode != 0 or not result.stdout:
            return None

        output = result.stdout.strip()

        # Check for 404 or word not found
        if "not found" in output.lower() or "page not found" in output.lower():
            _UNKNOWN_WORDS.add(lower)
            return None

        parsed = _parse_etymonline_markdown(output, lower)
        return parsed

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Main lookup
# ---------------------------------------------------------------------------

def fetch_etymology_for_word(word: str, allow_scrape: bool = True) -> dict | None:
    """
    Get etymology for a word. Strategy:
    1. Check SQLite cache
    2. Check lemma in cache
    3. Firecrawl scrape etymonline (if API key configured and allow_scrape)
    4. Save result to cache
    """
    lower = word.lower().strip("'\"")
    if len(lower) < 3 or not lower.isascii():
        return None

    # 1. Check cache
    cached = lookup_cached(lower)
    if cached:
        return cached

    # 2. Check lemma form in cache
    lemma = lemmatize_word(lower)
    if lemma != lower:
        cached = lookup_cached(lemma)
        if cached:
            save_etymology(lower, cached["etymology"], cached.get("pos"))
            return cached

    # 3. Firecrawl fallback (only if API key is set and allow_scrape=True)
    if allow_scrape and _FIRECRAWL_API_KEY:
        result = _scrape_etymonline_firecrawl(lower)
        if result:
            save_etymology(
                lower,
                result["etymology"],
                result.get("pos"),
                result.get("origin_year"),
                source="firecrawl",
            )
            if lemma != lower:
                save_etymology(
                    lemma,
                    result["etymology"],
                    result.get("pos"),
                    result.get("origin_year"),
                    source="firecrawl",
                )
            return result

    return None


def process_text(text: str) -> list[dict]:
    """Process a paragraph; returns list of tokens with optional etymology."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z'-]*|[^\w\s]+|\s+", text)
    results = []
    for token in tokens:
        if token.isspace() or re.match(r"^[^\w]+$", token):
            results.append({"token": token, "etymology": None})
            continue
        cleaned = re.sub(r"[^a-zA-Z'-]", "", token)
        if not cleaned or not _is_word_worth_checking(cleaned):
            results.append({"token": token, "etymology": None})
            continue
        etym = fetch_etymology_for_word(cleaned, allow_scrape=False)
        if etym:
            results.append({"token": token, "etymology": etym["etymology"]})
        else:
            results.append({"token": token, "etymology": None})
    return results


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

init_db()

app = FastAPI(title="EtymTool", description="Word etymology API with SQLite cache")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).absolute().parent / "static"
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>EtymTool</h1><p>Place index.html in " + str(FRONTEND_DIR) + "</p>")


@app.get("/api/etymology/{word}")
async def get_etymology(word: str):
    """Get etymology for a single word."""
    # Force load offline dataset on first request
    ensure_offline_dataset_loaded()
    result = fetch_etymology_for_word(word)
    if result:
        return {"word": word, "found": True, "etymology": result["etymology"],
                "pos": result.get("pos"), "origin_year": result.get("origin_year"),
                "source": result.get("source", "cache")}
    return {"word": word, "found": False}


@app.post("/api/process-text")
async def api_process_text(payload: dict):
    """
    Process text. Returns tokens with etymology tooltips.
    Payload: {"text": "..."}
    """
    ensure_offline_dataset_loaded()
    text = payload.get("text", "")
    if not text or len(text) > 50000:
        return {"error": "Text required, max 50000 chars"}
    tokens = process_text(text)
    return {"tokens": tokens}


@app.get("/api/stats")
async def api_stats():
    return cache_stats()


@app.post("/api/import-dataset")
async def api_import_dataset():
    """Force import/re-import of the offline dataset."""
    global _OFFLINE_LOADED
    _OFFLINE_LOADED = False
    count = ensure_offline_dataset_loaded()
    return {"status": "imported" if count else "not_found", "count": count}


@app.get("/api/health")
async def health():
    return {"status": "ok", "db_path": str(DB_PATH)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
