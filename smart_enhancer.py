"""
smart_enhancer.py - Hybrid AI & Rule-based Text Enhancer for Quotal.

Combines:
1. Deterministic phonetic & slang correction (0ms, 0 VRAM, stdlib + re only).
2. Self-correction and false-start remover ("no wait", "I mean").
3. Advanced cleanup features (all deterministic, all in this file):
   - stutter / repeat removal ("I I I want to to go" -> "I want to go")
   - conversational filler strip (medial "like, you know, basically ...")
   - spoken punctuation ("comma", "new line", "bullet point" ...)
   - normalization (times, dates, years, $ amounts, e-mails, phone numbers)
   - personal dictionary (dictionary.json) and snippets (snippets.json)
   - output styles (default / formal / casual / concise / bullets)
   - prev_text context continuation
4. Question-mark and grammar punctuation heuristics.
5. Optional local LLM pass for advanced rewriting (Qwen 2.5 0.5B).

User data files (optional, UTF-8 JSON, next to settings.json; edits are picked
up live, no restart needed):

  dictionary.json   {"quotal": "Quotal", "kube ctl": "kubectl"}
                    or ["Quotal", "PostgreSQL"]   (canonical spellings)
                    or [{"from": "kube ctl", "to": "kubectl"}, ["a", "b"]]
  snippets.json     {"my email": "me@example.com", "my address": "12 Main St"}
"""

import os
import sys
import re
import json
import functools
import threading
import subprocess
from typing import Optional

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "qwen2.5-0.5b")
CT2_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "qwen2.5-0.5b-ct2")

_llm_model = None
_llm_tokenizer = None
_llm_lock = threading.Lock()
_download_lock = threading.Lock()
_is_downloading = False
_download_progress = ""


# ---------------------------------------------------------------------------
# User data files: dictionary.json / snippets.json
# ---------------------------------------------------------------------------
def _user_data_dir() -> str:
    """Same folder as settings.json (paths.app_dir() when available)."""
    try:
        import paths  # project module; absent when this file is used standalone
        return paths.app_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


DICTIONARY_FILE = os.path.join(_user_data_dir(), "dictionary.json")
SNIPPETS_FILE = os.path.join(_user_data_dir(), "snippets.json")

# [(compiled_regex, replacement)] - refreshed by load_custom_dictionary(). [] fallback.
CUSTOM_REPLACEMENTS: list = []
# [(compiled_regex, expansion)] - refreshed by load_snippets(). [] fallback.
SNIPPET_REPLACEMENTS: list = []

_user_data_lock = threading.Lock()
_dict_state: dict = {}
_snip_state: dict = {}


def _phrase_regex(phrase: str, tolerate_repeats: bool = False):
    """Case-insensitive whole-phrase regex. Whitespace in the phrase is flexible."""
    toks = phrase.split()
    if not toks:
        return None
    parts = []
    for t in toks:
        e = re.escape(t)
        # tolerate_repeats: "my my email" still matches the trigger "my email"
        parts.append(rf"(?:{e}\s+)*{e}" if tolerate_repeats else e)
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _refresh_json(path: str, state: dict, builder) -> list:
    """mtime-cached JSON loader. Any problem (missing/corrupt file) -> []."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    if "value" in state and state.get("mtime") == mtime:
        return state["value"]
    with _user_data_lock:
        value: list = []
        if mtime is not None:
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    value = builder(json.load(f))
            except Exception as e:
                print(f"[SmartEnhancer] Ignoring {os.path.basename(path)}: {e}")
                value = []
        state["mtime"] = mtime
        state["value"] = value
    return value


def _build_dictionary(raw) -> list:
    pairs = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        for e in raw:
            if isinstance(e, str):
                pairs.append((e, e))
            elif isinstance(e, dict):
                pairs.append((e.get("from") or e.get("spoken"), e.get("to") or e.get("written")))
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                pairs.append((e[0], e[1]))
    out = []
    for frm, to in pairs:
        if not isinstance(frm, str) or not isinstance(to, str) or not frm.strip():
            continue
        rx = _phrase_regex(frm.strip())
        if rx:
            out.append((len(frm.strip()), rx, to))
    out.sort(key=lambda t: -t[0])  # longest phrase first
    return [(rx, to) for _, rx, to in out]


def _build_snippets(raw) -> list:
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(e.get("trigger"), e.get("expansion")) for e in raw if isinstance(e, dict)]
    out = []
    for trig, exp in items:
        if not isinstance(trig, str) or not isinstance(exp, str) or not trig.strip():
            continue
        rx = _phrase_regex(trig.strip(), tolerate_repeats=True)
        if rx:
            out.append((len(trig.strip()), rx, exp))
    out.sort(key=lambda t: -t[0])
    return [(rx, exp) for _, rx, exp in out]


def load_custom_dictionary() -> list:
    """(Re)load dictionary.json into CUSTOM_REPLACEMENTS. Returns [] if absent/invalid."""
    global CUSTOM_REPLACEMENTS
    CUSTOM_REPLACEMENTS = _refresh_json(DICTIONARY_FILE, _dict_state, _build_dictionary)
    return CUSTOM_REPLACEMENTS


def load_snippets() -> list:
    """(Re)load snippets.json into SNIPPET_REPLACEMENTS. Returns [] if absent/invalid."""
    global SNIPPET_REPLACEMENTS
    SNIPPET_REPLACEMENTS = _refresh_json(SNIPPETS_FILE, _snip_state, _build_snippets)
    return SNIPPET_REPLACEMENTS


def reload_user_data() -> None:
    """Force a re-read of dictionary.json and snippets.json on next use."""
    _dict_state.clear()
    _snip_state.clear()
    load_custom_dictionary()
    load_snippets()


def _read_dictionary_file() -> dict:
    """Raw {spoken: written} mapping from dictionary.json ({} if absent/invalid)."""
    try:
        with open(DICTIONARY_FILE, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, list):
        pairs = []
        for e in raw:
            if isinstance(e, str):
                pairs.append((e, e))
            elif isinstance(e, dict) and (e.get("from") or e.get("spoken")):
                pairs.append((e.get("from") or e.get("spoken"), e.get("to") or e.get("written")))
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                pairs.append((e[0], e[1]))
    else:
        return {}
    out = {}
    for frm, to in pairs:
        if isinstance(frm, str) and isinstance(to, str) and frm.strip() and to.strip():
            out[frm.strip()] = to.strip()
    return out


def _write_dictionary_file(entries: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DICTIONARY_FILE)) or ".", exist_ok=True)
    with open(DICTIONARY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def get_dictionary_entries() -> dict:
    """Personal dictionary as {spoken: written} for the dashboard UI."""
    return _read_dictionary_file()


def add_dictionary_entry(spoken: str, written: str) -> dict:
    """Add/replace one entry (case-insensitive match). Returns full mapping."""
    spoken = (spoken or "").strip()
    written = (written or "").strip()
    if not spoken or not written or len(spoken) > 80 or len(written) > 80:
        return _read_dictionary_file()
    entries = _read_dictionary_file()
    for k in list(entries):
        if k.lower() == spoken.lower():
            del entries[k]
    entries[spoken] = written
    _write_dictionary_file(entries)
    reload_user_data()
    return entries


def remove_dictionary_entry(spoken: str) -> dict:
    """Delete one entry (case-insensitive). Returns full mapping."""
    spoken = (spoken or "").strip().lower()
    if not spoken:
        return _read_dictionary_file()
    entries = _read_dictionary_file()
    for k in list(entries):
        if k.lower() == spoken:
            del entries[k]
    _write_dictionary_file(entries)
    reload_user_data()
    return entries


# ---------------------------------------------------------------------------
# Placeholder stash: protects expansions / e-mails from later rewriting steps
# ---------------------------------------------------------------------------
_PH_OPEN = "\ue000"
_PH_CLOSE = "\ue001"
_PH_BASE = 0xE100
_PH_RE = re.compile(_PH_OPEN + r"([\ue100-\uf8ff])" + _PH_CLOSE)


class _Stash:
    def __init__(self):
        self.items: list = []

    def put(self, s: str) -> str:
        self.items.append(s)
        return f"{_PH_OPEN}{chr(_PH_BASE + len(self.items) - 1)}{_PH_CLOSE}"

    def restore(self, text: str) -> str:
        if not self.items:
            return text
        return _PH_RE.sub(lambda m: self.items[ord(m.group(1)) - _PH_BASE], text)


def apply_snippets(text: str, _stash: Optional[_Stash] = None) -> str:
    """Expand snippets.json triggers (case-insensitive phrase replace).

    Skips expansion when the trigger is followed by a copula (is/are/was/were):
    "my email is john@..." is the user dictating an address, not invoking
    the "my email" snippet. "send it to my email please" still expands.
    """
    if not text:
        return text
    snippets = load_snippets()
    if not snippets:
        return text
    st = _stash if _stash is not None else _Stash()
    out = text
    for rx, exp in snippets:
        def _sub(m: re.Match, e=exp) -> str:
            nxt = re.match(r"\s*([A-Za-z']+)", m.string[m.end():])
            if nxt and nxt.group(1).lower() in ("is", "are", "was", "were"):
                return m.group(0)
            return st.put(e)
        out = rx.sub(_sub, out)
    return out if _stash is not None else st.restore(out)


def apply_custom_replacements(text: str, _stash: Optional[_Stash] = None) -> str:
    """Apply the personal dictionary (CUSTOM_REPLACEMENTS)."""
    if not text:
        return text
    reps = load_custom_dictionary()
    if not reps:
        return text
    st = _stash if _stash is not None else _Stash()
    out = text
    for rx, to in reps:
        out = rx.sub(lambda m, t=to: st.put(t), out)
    return out if _stash is not None else st.restore(out)


# Common speech-to-text phonetic misspellings & slang
SLANG_AND_PHONETIC_REPLACEMENTS = [
    # Slang & greeting fixes
    (r"\b(?:whatsup|what sup|watsup|wat sup)\b", "what's up"),
    (r"\b(?:badi)\b", "buddy"),
    (r"\b(?:plz|pls)\b", "please"),
    (r"\b(?:cuz|coz)\b", "because"),
    (r"\b(?:gimme)\b", "give me"),
    (r"\b(?:lemme)\b", "let me"),
    (r"\b(?:kinda)\b", "kind of"),
    (r"\b(?:sorta)\b", "sort of"),

    # Common English contractions
    (r"\bdont\b", "don't"),
    (r"\bdidnt\b", "didn't"),
    (r"\bdoesnt\b", "doesn't"),
    (r"\bisnt\b", "isn't"),
    (r"\barent\b", "aren't"),
    (r"\bwasnt\b", "wasn't"),
    (r"\bwerent\b", "weren't"),
    (r"\bwont\b", "won't"),
    (r"\bcant\b", "can't"),
    (r"\bcouldnt\b", "couldn't"),
    (r"\bwouldnt\b", "wouldn't"),
    (r"\bshouldnt\b", "shouldn't"),
    (r"\bhavent\b", "haven't"),
    (r"\bhasnt\b", "hasn't"),
    (r"\bhadnt\b", "hadn't"),
    (r"\bthats\b", "that's"),
    (r"\bwhats\b", "what's"),
    (r"\btheres\b", "there's"),
    (r"\bheres\b", "here's"),
    (r"\bwhos\b", "who's"),
    (r"\blets\b", "let's"),
    (r"\b(?:im)\b", "I'm"),
    (r"\b(?:ive)\b", "I've"),
    (r"\b(?:youre)\b", "you're"),
    (r"\b(?:theyre)\b", "they're"),
    (r"\b(?:weve)\b", "we've"),

    # Technical phrases & keyboard shortcuts
    (r"\bdot env\b", ".env"),
    (r"\bpackage dot json\b", "package.json"),
    (r"\bdot gitignore\b", ".gitignore"),
    (r"\bcontrol shift p\b", "Ctrl+Shift+P"),
    (r"\bcontrol c\b", "Ctrl+C"),
    (r"\bcontrol v\b", "Ctrl+V"),

    # Numbers & units (times/dates/money/years are handled by normalize_entities)
    (r"\bfive hundred ms\b", "500 ms"),
    (r"\bone hundred\b", "100"),
    (r"\bfive pm\b", "5 PM"),
    (r"\bfive am\b", "5 AM"),
]

# Words that typically indicate a question when starting a sentence or clause
QUESTION_STARTERS = {
    "what", "why", "how", "when", "where", "who", "whom", "whose", "which",
    "is", "are", "do", "does", "did", "can", "could", "will", "would", "should", "may"
}

# --- Instant-tidy: Backtrack cues (no LLM) ---
TOTAL_ABANDON_RES = [
    r"forget all that", r"forget everything", r"forget that",
    r"ignore all that", r"ignore that",
    r"scratch everything", r"scratch all that",
    r"never mind all that", r"start over",
]
# Clause-drop cues: previous clause/sentence is retracted, keep suffix.
CLAUSE_DROP_CUES = [
    "scratch that", "never mind", "let me rephrase",
    "forget it", "ignore it",
]
# Inline correction cues: "<old> CUE <new>"
INLINE_CUES = [
    "scratch that", "wait no", "oh wait", "no wait",
    "never mind", "actually", "let me rephrase",
    "i mean", "sorry",
]
# "Weak" cues double as ordinary speech ("I actually like it", "Sorry I'm late",
# "Actually 6pm works"). They only count as a correction when the context says so.
WEAK_CUES = ("actually", "i mean", "sorry")

NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "thirty", "forty", "fifty", "hundred", "thousand",
}
_NUM_START_RE = re.compile(
    r"(?:\d|(?:%s)\b)" % "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)), re.IGNORECASE
)

# --- Instant-tidy: Code awareness (no LLM) ---
CODE_HINT_RE = re.compile(
    r"\b(const|let|var|function|async|await|export|import|return|if|else|"
    r"docker|kubectl|git|npm|class|for|while|request|response|next)\b",
    re.IGNORECASE,
)
SPOKEN_SYMBOLS_MULTI = [
    (r"\bopen paren(?:thesis)?\b", "("),
    (r"\bclose paren(?:thesis)?\b", ")"),
    (r"\bopen bracket\b", "["),
    (r"\bclose bracket\b", "]"),
    (r"\bopen (?:brace|curly(?: bracket)?)\b", "{"),
    (r"\bclose (?:brace|curly(?: bracket)?)\b", "}"),
    (r"\bdouble quote\b", '"'),
    (r"\bsingle quote\b", "'"),
    (r"\bback ?tick\b", "`"),
    (r"\bsemicolon\b", ";"),
    (r"\bcolon\b", ":"),
    (r"\bcomma\b", ","),
    (r"\bdouble (?:equals?|equal to)\b", "=="),
    (r"\bstrictly? equals?\b", "==="),
    (r"\bnot equals?\b|\bnot equal to\b", "!="),
    (r"\bfat arrow\b|\barrow\b", "=>"),
    (r"\bdivided by\b", "/"),
    (r"\bmultiplied by\b", "*"),
]
SPOKEN_OPERATORS_CODE_ONLY = [
    (r"\bequals?\b", "="),
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\btimes\b", "*"),
    (r"\bpipe\b", "|"),
    (r"\bampersand\b", "&"),
    (r"\bpercent\b", "%"),
]
TECH_ACRONYMS = [
    (r"\bjson\b", "JSON"),
    (r"\bjavascript\b", "JavaScript"),
    (r"\btypescript\b", "TypeScript"),
    (r"\bpostgres(?:ql)?\b", "PostgreSQL"),
    (r"\bmysql\b", "MySQL"),
    (r"\bjwt\b", "JWT"),
    (r"\boauth\b", "OAuth"),
    (r"\bkubernetes\b|\bk8s\b", "Kubernetes"),
    (r"\basync await\b", "async/await"),
    (r"\basync\b", "async"),
    (r"\bawait\b", "await"),
    (r"\bgithub\b", "GitHub"),
    (r"\bvs ?code\b", "VS Code"),
    (r"\bapi\b", "API"),
    (r"\bdot env\b", ".env"),
]
CLI_FLAG_PHRASES = [
    (r"\bwith detached flag\b|\bin detached mode\b", " -d"),
    (r"\bwith force flag\b", " --force"),
    (r"\bwith verbose flag\b", " --verbose"),
    (r"\bwith recursive flag\b", " -r"),
]

LIST_HEADERS = [
    "action items", "action item", "to dos", "to do", "todos", "todo",
    "action plan", "agenda", "checklist", "shopping list", "steps", "step",
]
ENUM_SPLIT_RE = re.compile(
    r",?\s*(?:first(?:ly)?|second(?:ly)?|third(?:ly)?|fourth(?:ly)?|fifth(?:ly)?|sixth(?:ly)?|seventh(?:ly)?)\s+(?:we\s+)?",
    re.IGNORECASE,
)
TASK_VERBS = [
    "review", "deploy", "run", "ping", "send", "check", "restart",
    "alert", "book", "call", "email", "merge", "test", "build",
]
# Verbs safe for blind verb-boundary splitting (excludes noun/verb ambiguities like build/test).
SPLIT_VERBS = [
    "review", "deploy", "run", "ping", "send", "check", "restart",
    "alert", "book", "call", "email", "merge",
]
CODE_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


# ---------------------------------------------------------------------------
# Code-line detection + noun-use guards (shared by several steps)
# ---------------------------------------------------------------------------
_CODE_LINE_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(?:const|let|var)\s+[\w$]+\s*(?:=|;|:|equals?\b)",
    r"^\s*export\s+(?:default|const|let|var|function|class|async|\{)",
    r"^\s*(?:async\s+(?:function|\()|def\s+\w+|elif\s)",
    r"^\s*class\s+[\w$]+\s*(?:extends\b|\{|:)",
    r"^\s*import\s+(?:[\w${}*,\s]+?\s+from\s+\S|[\"'])",
    r"^\s*(?:git|docker|kubectl|npm|npx|pnpm|yarn|pip|pip3|sudo|cargo|curl|chmod)\s+\w",
    r"\b(?:camel|snake|kebab) case\b|\b(?:screaming snake|constant) case\b",
    r"\b(?:open|close) (?:paren(?:thesis)?|bracket|brace|curly)\b",
    r"\bdouble equals?\b|\bfat arrow\b|\bnot equals?\b",
    r"[{}=<>`]|=>|\s--\w",
)]


def _is_code_line(text: str) -> bool:
    """True when the dictation looks like code / a CLI command (spoken or typed)."""
    t = text.strip()
    return bool(t) and any(r.search(t) for r in _CODE_LINE_RES)


_DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her",
    "its", "our", "their", "each", "every", "any", "no", "per", "another", "same",
    "some", "first", "last", "next", "second", "third", "whole",
}
_NOUN_PREV = _DETERMINERS | {
    "oxford", "serial", "time", "grace", "trial", "probation", "waiting", "notice",
    "reporting", "billing", "payment", "cooling", "holding", "school", "class",
    "long", "short", "certain", "given", "specific", "particular",
}
_NOUN_NEXT = {
    "period": {"of", "between", "during"},
    "colon": {"cancer", "cleanse", "polyps", "polyp"},
    "comma": {"splice", "splices"},
    "new line": {"character", "characters", "of", "break", "breaks"},
    "bullet point": {"list", "lists", "format", "summary", "above", "below", "is", "are",
                     "was", "about", "number", "slide", "slides"},
    "question mark": {"key", "icon", "button"},
}


def _prev_next_words(s: str, start: int, end: int):
    pm = re.search(r"([A-Za-z']+)[,.;:!?]*\s*$", s[:start])
    nm = re.match(r"\s*([A-Za-z']+)", s[end:])
    return (pm.group(1).lower() if pm else ""), (nm.group(1).lower() if nm else "")


def _is_noun_use(cmd: str, prev: str, nxt: str) -> bool:
    """'the time period of', 'a comma splice', 'new line character' -> literal words."""
    # Number words are values, not determiners: "one comma b" must convert.
    if prev in NUMBER_WORDS or prev in _NUM_UNITS or prev in _NUM_TENS:
        return False
    return prev in _NOUN_PREV or nxt in _NOUN_NEXT.get(cmd, ())


# ---------------------------------------------------------------------------
# 5. prev_text context continuation
# ---------------------------------------------------------------------------
def _apply_prev_text(text: str, prev_text: str):
    """
    If the previous chunk ended mid-sentence (no . ? ! : at the end), this is a
    continuation: drop a 1-3 word prefix of `text` that duplicates the tail of
    prev_text. Returns (text, continuation).
    Terminated sentences are never deduped (legit repeats across sentences).
    """
    if not prev_text or not prev_text.strip():
        return text, False
    prev = prev_text.rstrip()
    if prev[-1] in ".?!:\u2026\n":
        return text, False
    prev_words = [w.strip("'") for w in re.findall(r"[\w']+", prev.lower())][-3:]
    cur = text.split()
    norm = [re.sub(r"^[^\w']+|[^\w']+$", "", w.lower()).strip("'") for w in cur[:3]]
    for k in (3, 2, 1):
        if len(prev_words) >= k and len(cur) > k and norm[:k] == prev_words[-k:]:
            return " ".join(cur[k:]), True
    return text, True


# ---------------------------------------------------------------------------
# 2. Stutters and repeats
# ---------------------------------------------------------------------------
# Only function words are collapsed: "no no" / "very very" / "bye bye" / Hinglish
# reduplication ("dheere dheere") are deliberate and stay.
_STUTTER_WORDS = {
    "i", "we", "you", "he", "she", "they", "it", "the", "a", "an", "to", "of", "in",
    "on", "at", "for", "with", "and", "but", "or", "my", "your", "our", "their",
    "his", "her", "this", "be", "was", "are", "were", "have", "has", "will", "would",
    "could", "should", "if", "as", "by", "from", "just", "then",
    "i'm", "i'll", "i've", "i'd", "it's", "we're", "you're", "they're", "don't",
    "can't", "won't", "there's", "that's", "let's",
}
_DANGLING = {
    "the", "a", "an", "to", "of", "and", "or", "but", "my", "your", "our", "their",
    "his", "her", "its", "in", "on", "at", "for", "with", "that", "this", "is", "are",
    "was", "were", "be", "if", "as", "by", "from", "we", "i", "you", "they", "it",
    "he", "she", "so", "because", "than",
}
_DASH_STUTTER_RE = re.compile(
    r"(?<![\w'-])([A-Za-z]{1,3})-(?:\1-)+(\1[A-Za-z']*)(?![\w'-])", re.IGNORECASE
)
_WORD_REPEAT_RE = re.compile(
    r"(?<![\w'])([A-Za-z][A-Za-z']*)((?:\s*,?\s+\1(?![\w'])){1,})", re.IGNORECASE
)
_PHRASE_RESTART_RE = re.compile(
    r"(?<![\w'])(?P<p>[\w']+(?:\s+[\w']+){1,3})(?P<f>(?:\s+[\w']+){0,2})\s*,\s*(?P=p)(?![\w'])",
    re.IGNORECASE,
)
_PHRASE_REPEAT_RE = re.compile(
    r"(?<![\w'])(?P<p>[\w']+(?:\s+[\w']+){1,4})\s+(?P=p)(?![\w'])", re.IGNORECASE
)


def _has_proper_noun(tokens) -> bool:
    return any(t[:1].isupper() and t.lower() != "i" and not re.fullmatch(r"I['\u2019]\w+", t)
               for t in tokens)


def _dash_stutter_sub(m: re.Match) -> str:
    res = m.group(2)
    if m.group(1)[:1].isupper() and res[:1].islower():
        res = res[0].upper() + res[1:]
    return res


def _word_repeat_sub(m: re.Match) -> str:
    w = m.group(1)
    if w.lower() not in _STUTTER_WORDS:
        return m.group(0)
    copies = re.findall(r"[A-Za-z][A-Za-z']*", m.group(2))
    if _has_proper_noun(copies):  # "Will will", band names, titles
        return m.group(0)
    return w


def _phrase_restart_sub(m: re.Match) -> str:
    first_seg = (m.group("p") + m.group("f")).split()
    if first_seg[-1].lower() not in _DANGLING:  # first attempt must be abandoned mid-phrase
        return m.group(0)
    if _has_proper_noun(m.group("f").split()):
        return m.group(0)
    p = m.group("p")
    return m.string[m.end() - len(p):m.end()]  # keep the later (completed) attempt


def _phrase_repeat_sub(m: re.Match) -> str:
    p = m.group("p")
    if p.split()[0].lower() not in _STUTTER_WORDS:
        return m.group(0)
    if _has_proper_noun(m.string[m.end() - len(p):m.end()].split()):
        return m.group(0)
    return p


def fix_repeats(text: str) -> str:
    """
    Stutter / repeat removal.
      "I I I want to to go"            -> "I want to go"
      "w-w-word"                       -> "word"
      "I went to the, I went to store" -> "I went to store"
    Only function words are collapsed; proper nouns, "no no", "very very", "bye bye"
    and Hinglish reduplication ("dheere dheere") are left alone. Skips code lines.
    """
    if not text or not text.strip() or _is_code_line(text):
        return text
    out = _DASH_STUTTER_RE.sub(_dash_stutter_sub, text)
    out = _WORD_REPEAT_RE.sub(_word_repeat_sub, out)
    out = _PHRASE_RESTART_RE.sub(_phrase_restart_sub, out)
    out = _PHRASE_REPEAT_RE.sub(_phrase_repeat_sub, out)
    out = _WORD_REPEAT_RE.sub(_word_repeat_sub, out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# ---------------------------------------------------------------------------
# Self-correction (existing behaviour, with guards for the "weak" cues)
# ---------------------------------------------------------------------------
def _strip_explanation_prefix(suffix: str) -> str:
    """Drop leading '<Name> is out on PTO/sick/unavailable'-style explanation."""
    s = re.sub(
        r"^\s*[A-Z][a-z]*\s+is\s+out\s+(?:on\s+\w+|sick|unavailable|on leave)[,.]?\s*",
        "", suffix.strip(), flags=re.IGNORECASE,
    )
    return s.strip()


def _merge_instead(prefix: str, suffix: str) -> str | None:
    """Handle '<.. to A ..> CUE <.. to B instead>' -> keep B, drop duplicate verb."""
    m_target = re.search(r"\bto\s+([A-Za-z][\w]*)\s+instead\b", suffix, flags=re.IGNORECASE)
    m_orig = re.search(r"\bto\s+(\w+)\b", prefix, flags=re.IGNORECASE)
    if not (m_target and m_orig):
        return None
    target = m_target.group(1)
    prefix_fixed = re.sub(r"\bto\s+\w+\b", f"to {target} instead", prefix, count=1, flags=re.IGNORECASE)
    suffix_fixed = re.sub(
        r"^.*?\bto\s+\w+\s+instead\s*", "", suffix,
        count=1, flags=re.IGNORECASE | re.DOTALL,
    ).strip(" ,")
    if not suffix_fixed:
        return prefix_fixed.strip()
    if re.match(r"^(and|but|or)\b", suffix_fixed, flags=re.IGNORECASE):
        return f"{prefix_fixed.strip()}, {suffix_fixed}".strip()
    return f"{prefix_fixed.strip()}, {suffix_fixed}".strip()


def _weak_cue_is_correction(prefix: str, suffix: str, comma_before: bool, comma_after: bool) -> bool:
    """
    'actually' / 'i mean' / 'sorry' are also ordinary words. Treat them as a
    correction cue only when the context supports it:
      "meet at 5 actually 6"        (number follows)      -> correction
      "send it to Dave, I mean Marcus" (comma before only) -> correction
      "Actually 6pm works"          (sentence-initial)    -> NOT a correction
      "I actually like it"                                -> NOT a correction
    """
    if not prefix:
        return False
    if not suffix:
        return True  # trailing cue: drop it, as before
    low = suffix.lower()
    if _NUM_START_RE.match(suffix):
        return True
    if re.match(r"(?:make it|change it|let'?s (?:make|do|say|go)|lets (?:make|do|say|go)|use|say)\b", low):
        return True
    if "instead" in low:
        return True
    if comma_before and not comma_after and len(suffix.split()) <= 3:
        return True
    sw = low.split()
    pl = prefix.lower()
    for n in (3, 2):
        if len(sw) >= n and " ".join(sw[:n]) in pl:
            return True
    return False


def fix_self_corrections(text: str) -> str:
    """
    Deterministic backtrack & self-correction (instant tidy, no LLM).
    Handles:
    - Total abandonment: 'forget all that, <fresh>' -> '<fresh>'
    - Clause-drop: 'A. scratch that, B' -> 'A-minus-last + B'
    - Inline: 'five, no, six', 'for three actually make it 3:30',
      'to Dave ... to Marcus instead'
    """
    if not text or not text.strip():
        return ""
    out = text.strip()

    # 1. Total abandonment: keep only what follows the cue.
    for cue in TOTAL_ABANDON_RES:
        m = re.search(cue, out, flags=re.IGNORECASE)
        if m and m.end() < len(out):
            suffix = out[m.end():].strip(" ,.-")
            if suffix:
                out = suffix
                break

    # 2. Number / time / day corrections with 'no', 'no no', 'no wait', 'actually'
    # Handles: 'at 5 PM, no 6 PM', '5 PM, no, 6 PM', '5, no 6', '3 no no 4', 'Monday, no Tuesday', etc.
    num_alt = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
    days_alt = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    time_num_token = rf"(?:\b\d+(?::\d+)?\s*(?:[ap]\.?m\.?)?|\b(?:{num_alt})\s*(?:[ap]\.?m\.?)?|\b(?:{days_alt})\b)"
    cue_re = r",?\s*\b(?:no(?:\s*,?\s*no)*|no\s+wait|wait\s+no|actually\s+make\s+it|actually)\b,?\s*"
    prep_re = r"\b(at|for|to|on|by|in)\s+"
    num_time_pat = rf"(?:{prep_re})?({time_num_token})\s*{cue_re}(?:{prep_re})?({time_num_token})"

    def _sub_num_time(m):
        p1 = m.group(1) or ""
        p2 = m.group(3) or ""
        prep = p2 or p1
        y = m.group(4)
        if prep:
            return f"{prep} {y}"
        return y

    out = re.sub(num_time_pat, _sub_num_time, out, flags=re.IGNORECASE)

    # 2b. General 'X, no, Y' or 'X, no Y' (words separated by comma-no): drop X
    # e.g. "red, no, blue" -> "blue", "five, no, six chairs" -> "six chairs"
    out = re.sub(r"\b(?!no\b)([A-Za-z]+)\s*,\s*no\s*,\s*(?!no\b)", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?!no\b)([A-Za-z]+)\s*,\s*no\s+(?!no\b)([A-Za-z]+)\b", r"\2", out, flags=re.IGNORECASE)

    # 4. 'for/at/to/on/by <old> actually [make it] <new>' -> keep preposition + new.
    # The replacement must look like a correction (number or a Name); otherwise
    # "I was at home actually working" would lose "home".
    out = re.sub(
        r"\b(for|at|to|on|by)\s+[\w:]+\s+actually\s+"
        r"(?:(?:make it|change it to|let(?:'s)? make it)\s+)?"
        rf"(?=\d|(?:{num_alt})\b|(?-i:[A-Z]))",
        r"\1 ", out, flags=re.IGNORECASE,
    )

    # 4b. Single bare 'no' as repeat marker: 'meet at 6 no lets meet at 5'.
    # Only when the suffix repeats a 2-3 word phrase from the prefix
    # (avoids 'no idea' / 'no smoking' false positives).
    for m_no in list(re.finditer(r"(?<!\w)no(?!\w)", out, flags=re.IGNORECASE)):
        left = out[:m_no.start()]
        right = out[m_no.end():].strip(" ,")
        if not left.strip() or not right:
            continue
        window = out[max(0, m_no.start() - 9):m_no.end() + 9]
        if re.search(r"no\s*,?\s*no\b|\b(?:oh\s+wait|no\s+wait|wait\s+no)\b", window, flags=re.IGNORECASE):
            continue
        sw = right.split()
        applied = False
        for n in (3, 2):
            if len(sw) >= n:
                phrase = " ".join(sw[:n]).lower()
                if len(phrase) < 4:
                    continue
                idx = left.lower().rfind(phrase)
                if idx >= 0:
                    head = left[:idx].strip(" ,.-")
                    out = f"{head} {right}".strip() if head else right
                    applied = True
                    break
        if applied:
            break

    # 5. Iterate inline/clause cues in order of appearance.
    # Process longest cues first to avoid 'wait' shadowing 'no wait'.
    # Plus bare repeated-no: 'no no', 'no, no', 'no no no' (very common in speech).
    cues_sorted = sorted(set(INLINE_CUES + CLAUSE_DROP_CUES), key=len, reverse=True)
    cue_alt = "|".join(re.escape(c) for c in cues_sorted)
    no_repeat_alt = r"no(?:\s*,?\s*no)+"
    cue_re = re.compile(rf",?\s*(?:\.\.\.\s*)?(?:{cue_alt}|{no_repeat_alt})\s*,?\s*", re.IGNORECASE)

    def _next_cue(s: str):
        """First cue that is really a cue (weak cues need correction context)."""
        for mm in cue_re.finditer(s):
            g = mm.group(0)
            word = re.sub(r"[\s,.\-]+", " ", g).strip().lower()
            if word in WEAK_CUES:
                pre = s[:mm.start()].strip(" ,.-")
                suf = s[mm.end():].strip()
                lead = g[:g.lower().find(word)]
                if not _weak_cue_is_correction(pre, suf, "," in lead, g.rstrip().endswith(",")):
                    continue
            return mm
        return None

    # Bound iterations to avoid pathological loops.
    for _ in range(5):
        m = _next_cue(out)
        if not m:
            break
        prefix = out[:m.start()].strip(" ,.-")
        suffix = out[m.end():].strip()
        if not suffix:
            out = prefix
            break
        if not prefix:
            out = suffix
            break
        cue_text = m.group(0).lower()
        # a) 'instead' pattern is most specific — try first.
        if "instead" in suffix.lower():
            merged = _merge_instead(prefix, _strip_explanation_prefix(suffix))
            if merged:
                out = merged
                continue
        # b) Clause-drop cues or sentence-like suffix: drop last clause of prefix.
        suffix_is_sentence = len(suffix.split()) > 4 or re.match(r"^(we|you|he|she|they|it|let'?s|send|book|remind)\b", suffix, re.I)
        is_clause_drop = any(c in cue_text for c in ("scratch that", "never mind", "let me rephrase", "forget it", "ignore it"))
        if is_clause_drop or suffix_is_sentence:
            # Drop explanation prefix like 'Dave is out on PTO'
            suffix = _strip_explanation_prefix(suffix)
            # Drop last clause (after last comma/semicolon/period) of prefix
            parts = re.split(r"[.;]\s*|,\s*(?=(?:and|but|or)\b)", prefix)
            if len(parts) > 1 and len(suffix.split()) > 4:
                # Long replacement replaces last clause
                out = (", ".join(parts[:-1]) + " " + suffix).strip() if len(parts) > 2 else suffix
                # If prefix head still has verb context ('Send the invite'), re-merge via instead logic
                if "instead" in suffix.lower():
                    merged = _merge_instead(prefix, suffix)
                    if merged:
                        out = merged
                # Common case: 'Send invite to Dave ... send it to Marcus' without 'instead'
                if out == suffix and re.search(r"\bto\s+\w+\b", prefix, re.I) and re.search(r"\bto\s+\w+\b", suffix, re.I):
                    mt = re.search(r"\bto\s+([A-Za-z][\w]*)\b", suffix, re.I)
                    if mt:
                        head = re.sub(r"\bto\s+\w+\b.*$", f"to {mt.group(1)}", prefix, flags=re.I)
                        tail = re.sub(r"^.*?\bto\s+\w+\b\s*", "", suffix, count=1, flags=re.I)
                        out = f"{head} {tail}".strip() if tail else head
                continue
        # b2) Repeated clause: 'Hey let's meet at 6' + 'let's meet at 5pm'
        # -> 'Hey let's meet at 5pm'. Preserves greeting, drops retracted time.
        sw = suffix.split()
        repeated = False
        for n in (3, 2, 1):
            if len(sw) >= n:
                phrase = " ".join(sw[:n]).lower()
                # Skip trivial single words that cause false cuts ('to', 'at', 'i')
                if n == 1 and phrase.strip("'") in ("to", "at", "a", "the", "i", "it"):
                    continue
                idx = prefix.lower().rfind(phrase)
                if idx > 0:
                    head = prefix[:idx].strip(" ,.-")
                    out = f"{head} {suffix}".strip()
                    repeated = True
                    break
                elif idx == 0:
                    out = suffix
                    repeated = True
                    break
        if repeated:
            continue
        # c) Short replacement: anchor on preposition for times/numbers,
        # else swap last N words of prefix.
        n_repl = len(suffix.split())
        pw = prefix.split()
        is_no_repeat = bool(re.fullmatch(r"[\s,.]*no(?:\s*,?\s*no)+[\s,.]*", cue_text, re.IGNORECASE))
        if is_no_repeat and n_repl <= 4:
            # 'I want tea no no coffee please' -> replace last entity only.
            # 'We need five chairs no no six tables' -> replace from last number.
            m_num_suf = re.match(
                r"(?:\d+(?::\d+)?\s*(?:am|pm)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                suffix, flags=re.IGNORECASE,
            )
            num_hits = list(re.finditer(
                r"\b(?:\d+(?::\d+)?\s*(?:am|pm)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                prefix, flags=re.IGNORECASE,
            ))
            if m_num_suf and num_hits:
                out = f"{prefix[:num_hits[-1].start()].strip()} {suffix}".strip()
            elif len(pw) >= 1:
                out = f"{' '.join(pw[:-1])} {suffix}".strip()
            else:
                out = suffix
            continue
        m_prep = re.search(r"\b(at|for|to|on|by|until|till)\b", prefix, flags=re.IGNORECASE)
        m_time = re.match(
            r"(?:\d|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)",
            suffix, flags=re.IGNORECASE,
        ) or re.search(r"\b(?:am|pm|o'?clock)\b", suffix, flags=re.IGNORECASE)
        if m_prep and m_time and n_repl <= 4:
            # Keep through last preposition: 'we meet at 5' + '6 pm' -> 'we meet at 6 pm'
            prep_hits = list(re.finditer(r"\b(at|for|to|on|by|until|till)\b", prefix, flags=re.IGNORECASE))
            cut = prep_hits[-1].start()
            out = f"{prefix[:cut].strip()} {prep_hits[-1].group(0)} {suffix}".strip()
        elif n_repl <= 3 and len(pw) > n_repl:
            out = f"{' '.join(pw[:-n_repl])} {suffix}".strip()
        else:
            # Fallback: drop last comma-segment
            segs = re.split(r",\s*", prefix)
            out = f"{', '.join(segs[:-1])} {suffix}".strip() if len(segs) > 1 else suffix
    out = re.sub(r"\s+", " ", out).strip(" ,.-")
    return out


# ---------------------------------------------------------------------------
# 3. Conversational filler strip (medial only)
# ---------------------------------------------------------------------------
_FILLER_ALT = r"you know|i mean|like|basically|actually|literally|sort of"
_FILLER_BRACKETED_RE = re.compile(rf"\s*,\s*(?:{_FILLER_ALT})\s*,\s*(?=\S)", re.IGNORECASE)
_FILLER_TRAILING_RE = re.compile(
    r"\s*,\s*(?:you know|i mean|basically|actually|literally)\s*(?=\.(?:\s|$)|$)", re.IGNORECASE
)
_FILLER_ADVERB_RE = re.compile(
    r"(?P<c1>,\s*)?(?<![\w'])(?:basically|literally|actually)(?![\w'])(?P<c2>\s*,)?", re.IGNORECASE
)
_FILLER_SORT_OF_RE = re.compile(r"(?<![\w'])sort of(?![\w'])", re.IGNORECASE)
_FILLER_LIKE_COMMA_RE = re.compile(r"(?<![\w'])like\s*,\s*(?=\S)", re.IGNORECASE)
_NEG_PREV = {"not", "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't",
             "can't", "couldn't", "won't", "wouldn't", "never", "no"}
_SORT_OF_NOUN_PREV = _DETERMINERS | {
    "what", "which", "whatever", "other", "kind", "type", "all", "such", "of", "exactly",
}
_LIKE_COMMA_PREV = {
    "was", "is", "were", "are", "am", "it's", "that's", "he's", "she's", "i'm", "we're",
    "they're", "you're", "there's", "here's", "and", "but", "so", "because", "when", "if",
}


def _word_before(s: str, pos: int) -> str:
    m = re.search(r"([A-Za-z']+)\s*,?\s*$", s[:pos])
    return m.group(1).lower() if m else ""


def strip_conversational_fillers(text: str) -> str:
    """
    Remove *medial* conversational fillers: like, you know, basically, actually,
    literally, sort of, I mean. Kept on purpose:
      - sentence/text-initial ones ("Actually 6pm works for me")
      - real uses ("I like pizza", "do you know where", "this sort of thing",
        "not actually", "I mean it")
      - everything on code lines
    """
    if not text or not text.strip() or _is_code_line(text):
        return text
    out = text
    out = _FILLER_BRACKETED_RE.sub(" ", out)          # "was, like, so tired"
    out = _FILLER_TRAILING_RE.sub("", out)            # "it was fine, you know."

    def _adverb(m: re.Match) -> str:
        before = m.string[:m.start()]
        if not before.strip() or re.search(r"[.?!\n]\s*$", before):
            return m.group(0)  # text/sentence start: contentful, keep
        if _word_before(before, len(before)) in _NEG_PREV:
            return m.group(0)
        if m.group("c1") and m.group("c2"):
            return " "
        if m.group("c1"):
            return " "
        return " " if not m.group("c2") else " "
    out = _FILLER_ADVERB_RE.sub(_adverb, out)

    def _sort_of(m: re.Match) -> str:
        before = m.string[:m.start()]
        if not before.strip() or re.search(r"[.?!\n]\s*$", before):
            return m.group(0)
        if _word_before(before, len(before)) in _SORT_OF_NOUN_PREV:
            return m.group(0)
        return ""
    out = _FILLER_SORT_OF_RE.sub(_sort_of, out)

    def _like_comma(m: re.Match) -> str:
        before = m.string[:m.start()]
        if _word_before(before, len(before)) in _LIKE_COMMA_PREV:
            return ""
        return m.group(0)
    out = _FILLER_LIKE_COMMA_RE.sub(_like_comma, out)

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.?!])", r"\1", out)
    out = re.sub(r",\s*,", ",", out)
    return out.strip()


# ---------------------------------------------------------------------------
# 1. Spoken punctuation
# ---------------------------------------------------------------------------
_SPOKEN_QUICK_RE = re.compile(
    r"\b(?:period|full stop|question mark|exclamation|comma|semi-?colon|colon|"
    r"new[ -]?line|new paragraph|bullet point|open quote|begin quote)\b",
    re.IGNORECASE,
)
_SPOKEN_CMD_RE = re.compile(
    r"(?P<pre>[,.;:!?]*)[ \t]*\b(?P<cmd>full stop|period|question mark|"
    r"exclamation (?:mark|point)|comma|semi-?colon|colon|new[ -]?line|new paragraph|"
    r"bullet point)\b(?P<post>[,.;:!?]*)",
    re.IGNORECASE,
)
_QUOTE_PAIR_RE = re.compile(
    r"\b(?:open|begin) quote\b[,.]?\s*(?P<inner>.+?)\s*,?\s*\b(?:close|end) quote\b(?P<post>[,.!?]*)",
    re.IGNORECASE | re.DOTALL,
)
_SPOKEN_PUNCT = {
    "period": ".", "full stop": ".", "question mark": "?",
    "exclamation mark": "!", "exclamation point": "!",
    "comma": ",", "semicolon": ";", "colon": ":",
}
_LOWERABLE = {
    "the", "a", "an", "how", "what", "why", "when", "where", "who", "which", "and", "but",
    "or", "so", "then", "if", "because", "this", "that", "these", "those", "we", "you",
    "he", "she", "they", "it", "my", "your", "our", "their", "his", "her", "to", "of",
    "in", "on", "at", "for", "with", "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "will", "would", "should", "there", "here", "please", "thanks",
    "thank", "let's", "just", "also", "not", "no", "yes",
}


def _quote_sub(m: re.Match) -> str:
    inner = m.group("inner").strip(" ,")
    post = m.group("post")
    if inner[-1:] in (".", "?", "!"):
        post = post.lstrip(".!?")
    return f'"{inner}"{post}'


def _spoken_cmd_sub(m: re.Match) -> str:
    s = m.string
    cmd = re.sub(r"[\s-]+", " ", m.group("cmd").lower())
    if cmd == "semi colon":
        cmd = "semicolon"
    elif cmd == "newline":
        cmd = "new line"
    pre, post = m.group("pre"), m.group("post")
    has_before = bool(s[:m.start()].strip())
    if cmd in _SPOKEN_PUNCT and not has_before:
        return m.group(0)  # nothing to attach to: leave the word alone
    prev, nxt = _prev_next_words(s, m.start(), m.end())
    if not pre and _is_noun_use(cmd, prev, nxt):
        return m.group(0)  # "the time period of", "a comma splice"
    wrapped = bool(re.search(r"[.?!]", pre + post))  # Whisper turned the command into a sentence
    if cmd in _SPOKEN_PUNCT:
        sym = _SPOKEN_PUNCT[cmd]
        if sym in ".?!":
            return sym + "\x01"  # \x01 = capitalise the next letter
        return sym + ("\x02" if wrapped else "")  # \x02 = lower a capitalised function word
    keep = "".join(ch for ch in pre if ch in ".?!:")[-1:]
    if cmd == "new line":
        return keep + "\n\x01"
    if cmd == "new paragraph":
        return keep + "\n\n\x01"
    return keep + "\n* \x01"  # bullet point


def _lower_function_word(m: re.Match) -> str:
    w = m.group(2)
    if w == "I" or re.fullmatch(r"I['\u2019]\w+", w):
        return m.group(1) + w
    if w.lower() in _LOWERABLE:
        w = w[0].lower() + w[1:]
    return m.group(1) + w


def apply_spoken_punctuation(text: str) -> str:
    """
    comma, period / full stop, question mark, exclamation mark|point, colon,
    semicolon, new line (\\n), new paragraph (\\n\\n), bullet point ("* "),
    open quote ... close quote. Skips code lines. Guards against noun use
    ("the time period of", "a comma splice", "new line character").
    """
    if not text or not _SPOKEN_QUICK_RE.search(text) or _is_code_line(text):
        return text
    out = _QUOTE_PAIR_RE.sub(_quote_sub, text)
    out = _SPOKEN_CMD_RE.sub(_spoken_cmd_sub, out)
    out = re.sub(r"\x01(\s*)([A-Za-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    out = re.sub(r"\x02(\s*)([A-Za-z][A-Za-z'\u2019]*)", _lower_function_word, out)
    out = out.replace("\x01", "").replace("\x02", "")
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    out = re.sub(r"(?m)^\* +", "* ", out)
    return out.strip()


# ---------------------------------------------------------------------------
# 4. Normalization: times, dates, years, $ amounts, e-mail, URLs, phone numbers
# ---------------------------------------------------------------------------
_NUM_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_NUM_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
             "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_UNIT_ALT = "|".join(sorted(_NUM_UNITS, key=len, reverse=True))
_TENS_ALT = "|".join(_NUM_TENS)
_D19 = "one|two|three|four|five|six|seven|eight|nine"
_TEENS_ALT = "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_NUMW = rf"(?:{_UNIT_ALT}|{_TENS_ALT}|hundred|thousand)"
_NUMRUN = rf"(?:a\s+(?=(?:hundred|thousand)\b))?{_NUMW}(?:[\s-]+(?:and\s+)?{_NUMW})*"


def _words_to_int(tokens) -> Optional[int]:
    """['two','thousand','twenty','five'] -> 2025. None if not a valid number."""
    total = cur = 0
    last = None  # 'u' (1-9), 'teen' (0/10-19), 't' (tens), 'h' (hundred), 'k' (thousand)
    for t in tokens:
        if t == "and":
            if last not in ("h", "k"):
                return None
            continue
        if t in _NUM_UNITS:
            v = _NUM_UNITS[t]
            kind = "u" if 0 < v < 10 else "teen"
            if v == 0:
                if last is not None or len(tokens) > 1:
                    return None
            elif kind == "u" and last not in (None, "t", "h", "k"):
                return None
            elif kind == "teen" and last not in (None, "h", "k"):
                return None
            cur += v
            last = kind
        elif t in _NUM_TENS:
            if last not in (None, "h", "k"):
                return None
            cur += _NUM_TENS[t]
            last = "t"
        elif t == "hundred":
            if last not in (None, "u"):
                return None
            cur = (cur or 1) * 100
            last = "h"
        elif t == "thousand":
            if last not in (None, "u", "teen", "t", "h"):
                return None
            total += (cur or 1) * 1000
            cur = 0
            last = "k"
        else:
            return None
    return total + cur if last is not None else None


def _toks(s: str) -> list:
    return [t for t in re.split(r"[\s-]+", s.lower()) if t]


# --- money ---
_MONEY_WORDS_RE = re.compile(
    rf"(?<![\w'])(?P<n>{_NUMRUN})\s+(?:dollars?|bucks)"
    rf"(?:\s+and\s+(?P<c>{_NUMRUN})\s+cents?)?(?![\w'])",
    re.IGNORECASE,
)
_MONEY_DIGITS_RE = re.compile(
    r"(?<![\w$.,])(?P<n>\d[\d,]*(?:\.\d+)?)\s+(?:dollars?|bucks)(?![\w'])", re.IGNORECASE
)


def _money_words_sub(m: re.Match) -> str:
    toks = _toks(m.group("n"))
    if toks and toks[0] == "a":
        toks[0] = "one"
    val = _words_to_int(toks)
    if val is None:
        return m.group(0)
    s = f"${val:,}"
    if m.group("c"):
        cv = _words_to_int(_toks(m.group("c")))
        if cv is None or cv > 99:
            return m.group(0)
        s += f".{cv:02d}"
    return s


# --- years ---
_YEAR_R = (
    rf"(?:(?:{_TENS_ALT})(?:[\s-]+(?:{_D19}))?|{_TEENS_ALT}|(?:oh|o)[\s-]+(?:{_D19}))"
)
_YEAR_BAD_NEXT = (
    r"(?:people|persons?|dollars?|bucks|rupees|items|things|times|days|hours|minutes|"
    r"seconds|percent|years|ms|miles|km|kg|hundred|thousand)"
)
_YEAR_WORDS_RE = re.compile(
    rf"(?<![\w'])(?P<c>nineteen|twenty)[\s-]+(?P<r>{_YEAR_R})(?![\w'])(?!\s+{_YEAR_BAD_NEXT}\b)",
    re.IGNORECASE,
)
_YEAR_2K_RE = re.compile(
    rf"(?<![\w'])two[\s-]+thousand[\s-]+(?:and[\s-]+)?(?P<r>{_YEAR_R}|{_D19})(?![\w'])"
    rf"(?!\s+{_YEAR_BAD_NEXT}\b)",
    re.IGNORECASE,
)


def _year_tail(r: str) -> Optional[int]:
    t = _toks(r)
    if t and t[0] in ("oh", "o"):
        return _NUM_UNITS.get(t[1]) if len(t) > 1 else None
    return _words_to_int(t)


def _year_words_sub(m: re.Match) -> str:
    n = _year_tail(m.group("r"))
    if n is None or not 0 <= n <= 99:
        return m.group(0)
    return f"{'19' if m.group('c').lower() == 'nineteen' else '20'}{n:02d}"


def _year_2k_sub(m: re.Match) -> str:
    n = _year_tail(m.group("r"))
    if n is None or not 1 <= n <= 99:
        return m.group(0)
    return f"20{n:02d}"


# --- dates ---
_MONTH_ALT = ("january|february|march|april|may|june|july|august|september|"
              "october|november|december")
_ORD_MAP = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
    "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30,
}
for _i, _u in enumerate(("first", "second", "third", "fourth", "fifth", "sixth",
                         "seventh", "eighth", "ninth"), start=1):
    _ORD_MAP[f"twenty {_u}"] = 20 + _i
_ORD_MAP["thirty first"] = 31
_ORD_ALT = "|".join(
    k.replace(" ", r"[\s-]+") for k in sorted(_ORD_MAP, key=len, reverse=True)
)
_DAY_CARD = rf"(?:(?:twenty|thirty)(?:[\s-]+(?:{_D19}))?|{_TEENS_ALT}|{_D19})"
_DATE_MD_RE = re.compile(
    rf"(?<![\w'])(?P<mo>{_MONTH_ALT})[\s,]+"
    rf"(?P<d>{_ORD_ALT}|\d{{1,2}}(?:st|nd|rd|th)?|{_DAY_CARD})(?![\w'])"
    rf"(?:,?\s+(?P<y>\d{{4}})(?![\w']))?",
    re.IGNORECASE,
)
_DATE_DM_RE = re.compile(
    rf"(?<![\w'])(?P<d>{_ORD_ALT}|\d{{1,2}}(?:st|nd|rd|th))\s+of\s+(?P<mo>{_MONTH_ALT})(?![\w'])"
    rf"(?:,?\s+(?P<y>\d{{4}})(?![\w']))?",
    re.IGNORECASE,
)
_AMBIGUOUS_MONTHS = {"may", "march", "august"}
_PRONOUN_PREV = {"i", "you", "we", "they", "he", "she", "it", "who", "that", "which",
                 "to", "will", "would", "could", "should", "might", "must", "can"}


def _ord_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _parse_day(d: str):
    """-> (number, is_ordinal) or None."""
    low = re.sub(r"[\s-]+", " ", d.lower())
    if low in _ORD_MAP:
        return _ORD_MAP[low], True
    m = re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", low)
    if m:
        return int(m.group(1)), bool(m.group(2))
    n = _words_to_int(low.split())
    return (n, False) if n is not None else None


def _date_md_sub(m: re.Match) -> str:
    mo = m.group("mo")
    parsed = _parse_day(m.group("d"))
    if not parsed or not 1 <= parsed[0] <= 31:
        return m.group(0)
    n, is_ord = parsed
    if mo.lower() in _AMBIGUOUS_MONTHS:
        prev, _ = _prev_next_words(m.string, m.start(), m.end())
        if prev in _PRONOUN_PREV:  # "I may first go", "to march ten miles"
            return m.group(0)
        if not is_ord and not m.group("d")[:1].isdigit():  # "march five" is a verb phrase
            return m.group(0)
    day = f"{n}{_ord_suffix(n)}" if is_ord else str(n)
    year = f", {m.group('y')}" if m.group("y") else ""
    return f"{mo.capitalize()} {day}{year}"


def _date_dm_sub(m: re.Match) -> str:
    parsed = _parse_day(m.group("d"))
    if not parsed or not 1 <= parsed[0] <= 31:
        return m.group(0)
    n = parsed[0]
    year = f" {m.group('y')}" if m.group("y") else ""
    return f"{n}{_ord_suffix(n)} of {m.group('mo').capitalize()}{year}"


# --- times ---
_AMPM = r"(?P<ap>[apAP])(?:\.\s?[mM]\.?|\s?[mM])(?![A-Za-z])"
_MIN_W = (
    rf"(?:oh|o)\s+(?:{_D19})|{_TEENS_ALT}|(?:twenty|thirty|forty|fifty)(?:[\s-]+(?:{_D19}))?"
)
_TIME_WORDS_RE = re.compile(
    rf"(?<![\w'])(?P<h>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    rf"(?:[\s-]+(?P<m>{_MIN_W}))?\s*{_AMPM}",
    re.IGNORECASE,
)
_TIME_DIGITS_RE = re.compile(
    rf"(?<![\w:.$])(?P<h>1[0-2]|0?[1-9])(?:[:.](?P<m>[0-5]\d))?\s*{_AMPM}",
    re.IGNORECASE,
)


def _fmt_time(h: int, mm: Optional[int], m: re.Match) -> str:
    ap = m.group("ap").upper() + "M"
    tail = "." if m.group(0).endswith(".") else ""
    return (f"{h}:{mm:02d} {ap}{tail}" if mm is not None else f"{h} {ap}{tail}")


def _time_words_sub(m: re.Match) -> str:
    h = _NUM_UNITS[m.group("h").lower()]
    mm = None
    if m.group("m"):
        t = _toks(m.group("m"))
        mm = _NUM_UNITS.get(t[1]) if t[0] in ("oh", "o") and len(t) > 1 else _words_to_int(t)
        if mm is None or not 0 <= mm <= 59:
            return m.group(0)
    return _fmt_time(h, mm, m)


def _time_digits_sub(m: re.Match) -> str:
    return _fmt_time(int(m.group("h")), int(m.group("m")) if m.group("m") else None, m)


# --- phone numbers ---
_DW = "zero|one|two|three|four|five|six|seven|eight|nine"
_DW_TOK = rf"(?:(?:double|triple)\s+)?(?:{_DW})"
_DW_TOK_OH = rf"(?:(?:double|triple)\s+)?(?:{_DW}|oh)"
_PHONE_WORDS_RE = re.compile(
    rf"(?<![\w'])(?P<plus>plus\s+)?(?P<run>{_DW_TOK}(?:[\s,-]+{_DW_TOK_OH}){{5,}})(?![\w'])",
    re.IGNORECASE,
)
_PHONE_DIGITS_RE = re.compile(r"(?<![\w.,-])(?:\d[ -]){6,}\d(?![\w.,-])")
_DIGIT_VAL = {w: str(i) for i, w in enumerate(_DW.split("|"))}
_DIGIT_VAL["zero"] = "0"
_DIGIT_VAL["oh"] = "0"


def _phone_words_sub(m: re.Match) -> str:
    digits, mult = [], 1
    for t in re.findall(r"double|triple|oh|" + _DW, m.group("run").lower()):
        if t == "double":
            mult = 2
        elif t == "triple":
            mult = 3
        else:
            digits.append(_DIGIT_VAL[t] * mult)
            mult = 1
    s = "".join(digits)
    if len(s) < 7:
        return m.group(0)
    return ("+" if m.group("plus") else "") + s


# --- e-mail / URLs ---
_EMAIL_TLD = (r"(?:com|org|net|edu|gov|io|co|in|dev|app|ai|me|us|uk|info|biz|xyz|tech|ca|au|de|fr)")
_URL_TLD = r"(?:com|org|net|edu|gov|io|dev|app|ai|info)"
_LOCAL = (
    r"[A-Za-z0-9][A-Za-z0-9_+-]*(?:\s+(?:dot|underscore|dash|hyphen)\s+[A-Za-z0-9][A-Za-z0-9_+-]*)*"
)
_EMAIL_SPOKEN_RE = re.compile(
    rf"(?<![\w.@+-])(?P<local>{_LOCAL})\s+at\s+"
    rf"(?P<dom>[A-Za-z0-9-]+(?:\s+dot\s+[A-Za-z0-9-]+)*\s+dot\s+{_EMAIL_TLD})(?![\w-])",
    re.IGNORECASE,
)
_EMAIL_HALF_RE = re.compile(
    rf"(?<![\w.@+-])(?P<local>{_LOCAL})\s+at\s+(?P<dom>(?:[A-Za-z0-9-]+\.)+{_EMAIL_TLD})(?![\w-])",
    re.IGNORECASE,
)
_URL_SPOKEN_RE = re.compile(
    rf"(?<![\w.@/-])(?:(?P<www>www)\s+dot\s+)?(?P<name>[A-Za-z0-9-]+(?:\s+dot\s+[A-Za-z0-9-]+)*)"
    rf"\s+dot\s+(?P<tld>{_URL_TLD})(?![\w-])",
    re.IGNORECASE,
)
_EMAIL_STOP = {
    "me", "him", "her", "us", "them", "you", "it", "this", "that", "here", "there", "home",
    "work", "school", "noon", "night", "time", "least", "most", "once", "all", "what",
    "which", "who", "the", "a", "an", "look", "looking", "looked", "arrive", "arrived",
    "arriving", "meet", "meeting", "start", "starts", "starting", "stay", "be", "is", "are",
    "was", "were", "sit", "stand", "aim", "good", "great", "bad", "best", "better", "wait",
    "waiting", "call", "called", "email", "text", "post", "posted", "mail", "contact", "go",
    "going", "come", "coming", "just", "only", "even", "also", "then", "now", "glance", "stare",
}
_URL_STOP = {"a", "the", "an", "this", "that", "my", "your", "our", "their", "his", "her",
             "its", "some", "any", "every", "each", "no", "one", "dot", "com"}


def _join_spoken(s: str) -> str:
    s = re.sub(r"\s+dot\s+", ".", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+underscore\s+", "_", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+(?:dash|hyphen)\s+", "-", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", s)


def normalize_entities(text: str, _stash: Optional[_Stash] = None) -> str:
    """
    Spoken -> written forms (all deterministic, skips code lines):
      "five thirty pm" -> "5:30 PM"        "march fifth twenty twenty six" -> "March 5th, 2026"
      "twenty five dollars" -> "$25"       "john at gmail dot com" -> "john@gmail.com"
      "nine eight seven six five four three two one zero" -> "9876543210"
    """
    if not text or _is_code_line(text):
        return text
    st = _stash if _stash is not None else _Stash()
    out = text

    def _email(m: re.Match) -> str:
        local = m.group("local")
        if " " not in local.strip() and local.lower() in _EMAIL_STOP:
            return m.group(0)
        return st.put(f"{_join_spoken(local)}@{_join_spoken(m.group('dom'))}".lower())

    def _url(m: re.Match) -> str:
        name = m.group("name")
        if name.lower() in _URL_STOP:
            return m.group(0)
        www = "www." if m.group("www") else ""
        return st.put(f"{www}{_join_spoken(name)}.{m.group('tld')}".lower())

    out = _EMAIL_SPOKEN_RE.sub(_email, out)
    out = _EMAIL_HALF_RE.sub(_email, out)
    out = _URL_SPOKEN_RE.sub(_url, out)
    out = _PHONE_WORDS_RE.sub(_phone_words_sub, out)
    out = _PHONE_DIGITS_RE.sub(lambda m: re.sub(r"[ -]", "", m.group(0)), out)
    out = _MONEY_WORDS_RE.sub(_money_words_sub, out)
    out = _MONEY_DIGITS_RE.sub(lambda m: "$" + m.group("n"), out)
    out = _YEAR_2K_RE.sub(_year_2k_sub, out)
    out = _YEAR_WORDS_RE.sub(_year_words_sub, out)
    out = _DATE_MD_RE.sub(_date_md_sub, out)
    out = _DATE_DM_RE.sub(_date_dm_sub, out)
    out = _TIME_WORDS_RE.sub(_time_words_sub, out)
    out = _TIME_DIGITS_RE.sub(_time_digits_sub, out)
    return out if _stash is not None else st.restore(out)


# ---------------------------------------------------------------------------
# Question / sentence punctuation
# ---------------------------------------------------------------------------
def fix_question_punctuation(text: str, terminal_period: bool = True) -> str:
    """
    Intelligently ensures question marks on questions.
    e.g. 'Hello buddy what's up.' -> 'Hello buddy, what's up?'
    terminal_period=False: never append a '.' (lines followed by a newline, bullets).
    """
    if not text:
        return ""

    text = text.strip()
    # Already terminated dialogue quotes: She said, "Don't touch that button."
    if re.search(r'["\u201c\u201d][.?!]["\u201c\u201d]?\s*$', text) or text.rstrip().endswith('."'):
        return text
    # If it already ends with '?' we are good
    if text.endswith("?"):
        return text

    # Remove trailing period if we might turn it into a question
    clean = text.rstrip(".")

    # Check if the sentence (or last clause) starts with a question starter
    words = clean.lower().split()
    if not words:
        return text

    is_question = False

    # 1. Starts with question starter: "What is your name"
    if words[0] in QUESTION_STARTERS:
        is_question = True

    # 2. Ends with a common question idiom: "what's up", "what about you", "how are you"
    lower_text = clean.lower()
    if any(lower_text.endswith(q) for q in ["what's up", "whats up", "how about you", "you know", "right", "isn't it"]):
        is_question = True

    # 2b. Hinglish question marker: "... hai kya" / "... kya" ("aaj milte hai kya").
    if re.search(r"\bkya\s*$", lower_text):
        is_question = True

    # 3. Clauses like "Hello buddy, what's up"
    for part in re.split(r"[,;]\s*", clean):
        part_words = part.strip().lower().split()
        if part_words and part_words[0] in QUESTION_STARTERS:
            is_question = True

    if is_question:
        return clean + "?"
    elif terminal_period and not text.endswith((".", "!", "?", ":", ";")):
        return text + "."
    return text


def apply_code_awareness(text: str) -> str:
    """
    Deterministic syntax & code awareness (instant tidy, no LLM).
    - Spoken symbols: 'open paren' -> '(' (always safe).
    - Operators 'equals/plus/minus': only when code hint present.
    - CLI flags: 'with detached flag' -> '-d'.
    - Acronyms: 'json' -> 'JSON', 'postgres' -> 'PostgreSQL'.
    - Casing: 'camel case foo bar' -> 'fooBar'.
    """
    if not text:
        return ""
    out = text
    guarded = {",": "comma", ":": "colon", ";": "semicolon"}
    for pattern, replacement in SPOKEN_SYMBOLS_MULTI:
        if replacement in guarded:
            cmd = guarded[replacement]
            # "the comma" / "a colon" stay words (noun use); everything else converts
            out = re.sub(
                pattern,
                lambda m, r=replacement, c=cmd: (
                    m.group(0) if _is_noun_use(c, *_prev_next_words(m.string, m.start(), m.end())) else r
                ),
                out, flags=re.IGNORECASE,
            )
        else:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    is_code = bool(CODE_HINT_RE.search(out))
    if is_code:
        for pattern, replacement in SPOKEN_OPERATORS_CODE_ONLY:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        # Spoken numbers in code: 'plus one' -> '+ 1'
        for word, digit in CODE_NUMBER_WORDS.items():
            out = re.sub(rf"\b{word}\b", digit, out, flags=re.IGNORECASE)
    for pattern, replacement in CLI_FLAG_PHRASES:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    for pattern, replacement in TECH_ACRONYMS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    def _to_camel(m: re.Match) -> str:
        words = m.group(1).strip().split()
        if not words:
            return m.group(0)
        return words[0].lower() + "".join(w.capitalize() for w in words[1:])

    def _to_snake(m: re.Match) -> str:
        return "_".join(w.lower() for w in m.group(1).strip().split())

    def _to_kebab(m: re.Match) -> str:
        return "-".join(w.lower() for w in m.group(1).strip().split())

    def _to_screaming(m: re.Match) -> str:
        return "_".join(w.upper() for w in m.group(1).strip().split())

    out = re.sub(r"\bcamel case\s+([a-z]+(?:\s+[a-z]+){0,4})", _to_camel, out, flags=re.IGNORECASE)
    out = re.sub(r"\bsnake case\s+([a-z]+(?:\s+[a-z]+){0,4})", _to_snake, out, flags=re.IGNORECASE)
    out = re.sub(r"\bkebab case\s+([a-z]+(?:\s+[a-z]+){0,4})", _to_kebab, out, flags=re.IGNORECASE)
    out = re.sub(
        r"\b(?:screaming snake case|constant case)\s+([a-z]+(?:\s+[a-z]+){0,4})",
        _to_screaming, out, flags=re.IGNORECASE,
    )
    # Tidy symbol spacing: 'count + 1' stays, '( req' -> '(req', 'res ,' -> 'res,'.
    out = re.sub(r"\(\s+", "(", out)
    out = re.sub(r"\s+\)", ")", out)
    out = re.sub(r"[ \t]+,", ",", out)
    return out


def format_dialogue_quotes(text: str) -> str:
    """
    'She said don't touch that button' -> 'She said, "Don't touch that button."'
    Only triggers on explicit '<Speaker> said/told/asked ...' to stay precise.
    """
    def _repl(m: re.Match) -> str:
        speaker, verb, content = m.group(1), m.group(2), m.group(3).strip()
        if not content:
            return m.group(0)
        content = content.strip(" ,.-")
        if content and content[0].islower():
            content = content[0].upper() + content[1:]
        if content and content[-1] not in ".!?":
            content += "."
        return f'{speaker} {verb}, "{content}"'
    return re.sub(
        r"\b([A-Za-z][\w]*)\s+(said|told me|asked)\s+(?:that\s+)?([^\"\n]+?)(?=[.!?]|$)",
        _repl, text,
    )


def format_automatic_list(text: str) -> str:
    """
    Explicit enumerations -> markdown list. Returns '' if no confident trigger.
    Handles:
    - Headers: 'action items for tomorrow morning: <items>'
    - Enumerators: 'first ..., second ..., third ...'
    """
    t = text.strip()
    if not t or "\n* " in t or t.startswith("* "):
        return ""
    # 1. Enumerator split: need 2+ of first/second/third/...
    enum_hits = ENUM_SPLIT_RE.findall(t)
    if len(re.findall(r"\bfirst\b|\bsecond\b|\bthird\b|\bfourth\b|\bfifth\b", t, re.I)) >= 2:
        parts = [p.strip(" ,.-") for p in ENUM_SPLIT_RE.split(t) if p.strip(" ,.-")]
        # Drop lead-in like 'first we check logs' keeps full clause
        items = [p[0].upper() + p[1:] if p and p[0].islower() else p for p in parts]
        return "\n".join(f"* {it}" for it in items if it)
    # 2. Header split: 'action items for X <items>'
    low = t.lower()
    header = next((h for h in LIST_HEADERS if low.startswith(h)), None)
    if header:
        rest = t[len(header):].strip(" :-")
        # Peel 'for tomorrow morning' style scope as title
        m = re.match(r"(for\s+.+?)\s+(review|deploy|run|ping|send|check|restart|alert|book|call|email|merge|test|build)\b(.*)$", rest, re.I | re.S)
        if m:
            title, first_verb, tail = m.group(1), m.group(2), m.group(3)
            rest_items = first_verb + tail
        else:
            title, rest_items = "", rest
        # Split items on strong delimiters + task-verb boundaries
        chunks = re.split(r"\s+and then\s+|\s*;\s*|\s+then\s+", rest_items, flags=re.I)
        items: list[str] = []
        verb_alt = "|".join(SPLIT_VERBS)
        for ch in chunks:
            # Further split 'review PRs deploy staging' on verb lookahead
            subs = re.split(rf",\s*|\s+and\s+|(?<=\w)\s+(?=(?:{verb_alt})\b)", ch, flags=re.I)
            items.extend(s.strip(" ,.-") for s in subs if s.strip(" ,.-"))
        items = [i[0].upper() + i[1:] if i and i[0].islower() else i for i in items]
        items = [i for i in items if len(i.split()) >= 2 or len(items) > 1]
        if len(items) >= 2:
            head = f"{header[0].upper() + header[1:]}"
            if title:
                head += f" {title.strip()}"
            return f"{head}:\n" + "\n".join(f"* {it}" for it in items)
    return ""


# ---------------------------------------------------------------------------
# 5. Output styles (rules fallback; the LLM switches system prompt instead)
# ---------------------------------------------------------------------------
STYLES = ("default", "formal", "casual", "concise", "bullets")
_STYLE_ALIASES = {
    "professional": "formal", "polite": "formal", "friendly": "casual",
    "short": "concise", "brief": "concise", "list": "bullets", "bullet": "bullets",
    "normal": "default", "": "default",
}


def _normalize_style(style) -> str:
    s = (style or "default")
    s = str(s).strip().lower()
    s = _STYLE_ALIASES.get(s, s)
    return s if s in STYLES else "default"


def _sub_cc(pattern: str, repl: str, text: str) -> str:
    """re.sub (case-insensitive) that keeps a leading capital."""
    def f(m: re.Match) -> str:
        new = m.expand(repl)
        if m.group(0)[:1].isupper() and new[:1].islower():
            new = new[0].upper() + new[1:]
        return new
    return re.sub(pattern, f, text, flags=re.IGNORECASE)


def _cap_sentences(text: str) -> str:
    return re.sub(r"(^|[.?!]\s+|\n\* |\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


_FORMAL_WORDS = [
    (r"\bgonna\b", "going to"), (r"\bwanna\b", "want to"), (r"\bgotta\b", "have to"),
    (r"\bkinda\b", "kind of"), (r"\bsorta\b", "sort of"),
    (r"\b(?:yeah|yep|yup)\b", "yes"), (r"\bnope\b", "no"),
    (r"\bhey\b", "hello"), (r"\bthanks\b", "thank you"), (r"\bbtw\b", "by the way"),
    (r"\b(?:cuz|cos)\b", "because"), (r"\b(?:plz|pls)\b", "please"),
]
_FORMAL_CONTRACTIONS = [
    (r"\bwon['\u2019]t\b", "will not"), (r"\bcan['\u2019]t\b", "cannot"),
    (r"\bshan['\u2019]t\b", "shall not"), (r"\blet['\u2019]s\b", "let us"),
    (r"\b(I)['\u2019]m\b", r"\1 am"),
    (r"\b(you|we|they)['\u2019]re\b", r"\1 are"),
    (r"\b(I|you|we|they|would|could|should|might|must)['\u2019]ve\b", r"\1 have"),
    (r"\b(I|you|he|she|it|we|they|that|there|this)['\u2019]ll\b", r"\1 will"),
    (r"\b(it|that|there|here|what|who|he|she|where|how)['\u2019]s\b", r"\1 is"),
]
_CASUAL_CONTRACTIONS = [
    (r"\b(do|does|did|is|are|was|were|have|has|had|would|could|should|must) not\b", r"\1n't"),
    (r"\bwill not\b", "won't"),
    (r"\b(?:cannot|can not)\b", "can't"),
    (r"\bI am\b(?=\s+\w)", "I'm"),
    (r"\b(you|we|they) are\b(?=\s+\w)", r"\1're"),
    (r"\b(I|you|he|she|it|we|they|that|there) will\b(?=\s+\w)", r"\1'll"),
    (r"\b(it|that|there|here|what|who) is\b(?=\s+\w)", r"\1's"),
    (r"\blet us\b", "let's"),
]
_CONCISE_REPLACEMENTS = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
    (r"\bat the present time\b", "currently"),
    (r"\bin the event that\b", "if"),
    (r"\bfor the purpose of\b", "for"),
    (r"(^|[.?!]\s+)(?:i|we)\s+(?:just\s+)?(?:wanted|want) to (?:let you know|say|mention|tell you)(?: that)?\s+", r"\1"),
    (r"(^|[.?!]\s+)(?:so|well|okay|ok|alright|anyway|anyways),?\s+", r"\1"),
    (r"\b(?:really|very|quite|honestly|obviously|simply|totally|basically|literally|actually|definitely)\b[ \t]*", ""),
]
_ABBREV_END = ("dr.", "mr.", "mrs.", "ms.", "prof.", "e.g.", "i.e.", "vs.", "etc.", "approx.", "st.", "jr.", "sr.")


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    out: list = []
    for p in parts:
        if out and out[-1].lower().endswith(_ABBREV_END):
            out[-1] += " " + p
        else:
            out.append(p)
    return [p for p in out if p.strip()]


def _to_bullets(text: str) -> str:
    t = text.strip()
    if t.startswith("* ") or "\n* " in t:
        return t  # already a list
    lines = [ln for ln in t.split("\n") if ln.strip()]
    if len(lines) > 1:
        items = lines
    else:
        items = _split_sentences(t)
        if len(items) < 2:  # single sentence: split on explicit sequencing words only
            items = [p for p in re.split(r"\s*;\s*|\s+and then\s+|\s+after that\s+|\s+then\s+", t, flags=re.I) if p.strip()]
    if len(items) < 2:
        return t
    out = []
    for it in items:
        it = it.strip()
        if it.startswith("* "):
            it = it[2:]
        if it.endswith(".") and not it.endswith(".."):
            it = it[:-1]
        if it:
            out.append("* " + (it[0].upper() + it[1:]))
    return "\n".join(out)


def apply_style(text: str, style: str = "default") -> str:
    """
    Rules fallback for the output styles (the LLM path swaps its system prompt):
      formal   expand contractions, replace slang (gonna, yeah, hey, thanks ...)
      casual   prefer contractions, drop the trailing period
      concise  drop hedges/intensifiers and wordy phrases
      bullets  one '* ' bullet per sentence / line (needs 2+ items)
    """
    style = _normalize_style(style)
    if style == "default" or not text or not text.strip() or _is_code_line(text):
        return text
    out = text
    if style == "formal":
        for p, r in _FORMAL_WORDS:
            out = _sub_cc(p, r, out)
        for p, r in _FORMAL_CONTRACTIONS:
            out = _sub_cc(p, r, out)
        out = re.sub(
            r"\b(?!(?:ai|ca|wo|sha)n['\u2019]t)([A-Za-z]+)n['\u2019]t\b",
            lambda m: m.group(1) + " not", out,
        )
        out = re.sub(r"!\s*$", ".", out)
        return _cap_sentences(out)
    if style == "casual":
        for p, r in _CASUAL_CONTRACTIONS:
            out = _sub_cc(p, r, out)
        out = out.rstrip()
        if "\n* " not in "\n" + out and out.endswith(".") and not out.endswith(".."):
            out = out[:-1]
        return out
    if style == "concise":
        for p, r in _CONCISE_REPLACEMENTS:
            out = re.sub(p, r, out, flags=re.IGNORECASE)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"[ \t]+([,.?!])", r"\1", out)
        return _cap_sentences(out.strip())
    if style == "bullets":
        return _to_bullets(out)
    return out


# ---------------------------------------------------------------------------
# Main rules pipeline
# ---------------------------------------------------------------------------
def _finalize_lines(out: str, apply_questions: bool, continuation: bool) -> str:
    """Per-line capitalisation + question/period punctuation (newline-aware)."""
    lines = out.split("\n")
    filled = [i for i, ln in enumerate(lines) if ln.strip()]
    if not filled:
        return out
    first, last = filled[0], filled[-1]
    for i, ln in enumerate(lines):
        if not ln.strip():
            lines[i] = ""
            continue
        bullet = ln.startswith("* ")
        body = ln[2:] if bullet else ln
        code = _is_code_line(body)
        if not code:
            body = re.sub(r"\b(?:i)\b", "I", body)
            if body and body[0].islower() and not (continuation and i == first):
                body = body[0].upper() + body[1:]
        if apply_questions and not code:
            ends_with_snippet = body.rstrip().endswith(_PH_CLOSE)
            only_last = (i == last) and not bullet and not ends_with_snippet
            body = fix_question_punctuation(body, terminal_period=only_last)
        lines[i] = ("* " + body) if bullet else body
    return "\n".join(lines)


def _enhance_core(text: str, apply_slang: bool, apply_questions: bool,
                  style: str, continuation: bool) -> str:
    out = text.strip()
    if not out:
        return ""
    stash = _Stash()

    # 1. Snippets (first step): "my email" -> expansion, protected from later steps
    out = apply_snippets(out, stash)
    core = out.strip(" .,!?;:\n")
    if stash.items and _PH_RE.fullmatch(core):
        return stash.restore(core)  # whole utterance was a snippet: paste verbatim

    # 2. Stutters / repeats
    out = fix_repeats(out)

    # 3. Fix self-corrections (Backtrack, no LLM)
    out = fix_self_corrections(out)

    # 4. Medial conversational fillers
    out = strip_conversational_fillers(out)

    # 5. Spoken punctuation (before question fix; skips code lines)
    out = apply_spoken_punctuation(out)

    # 6. Code awareness: symbols, CLI flags, acronyms, casing (no LLM)
    out = apply_code_awareness(out)

    # 6b. Undo cleaner.py sentence-capitalisation on code lines
    # ("Const x = 5" -> "const x = 5"). Only when the line carries a code
    # marker (=, parens, -flag) so prose like "Git history is long" keeps caps.
    if _is_code_line(out) and out[:1].isupper():
        _m0 = re.match(r"\s*([A-Za-z]+)", out)
        if (_m0 and _m0.group(1).lower() in (
                "const", "let", "var", "export", "import", "async", "def",
                "class", "git", "docker", "kubectl", "npm", "npx", "yarn",
                "pip", "sudo", "cargo", "curl", "chmod")
                and re.search(r"[=(){}\[\]`]|=>|\s-\s?\w|\s--\w", out)):
            out = out[:_m0.start(1)] + _m0.group(1)[:1].lower() + _m0.group(1)[1:] + out[_m0.end(1):]

    # 7. Personal dictionary hook (dictionary.json), before slang
    out = apply_custom_replacements(out, stash)

    # 8. Normalization + slang, contractions, phonetic replacements
    if apply_slang:
        out = normalize_entities(out, stash)
        for pattern, replacement in SLANG_AND_PHONETIC_REPLACEMENTS:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    # 9. Dialogue quotes: 'She said ...' -> 'She said, "..."'
    out = format_dialogue_quotes(out)

    # 10. Automatic list layout (explicit headers/enumerators only)
    listed = format_automatic_list(out)
    if listed:
        lines = []
        for ln in listed.split("\n"):
            ln = re.sub(r"[ \t]+", " ", ln).strip()
            ln = re.sub(r"\s+([,.?!:])", r"\1", ln)
            if ln.startswith("* ") and len(ln) > 2 and ln[2].islower():
                ln = "* " + ln[2].upper() + ln[3:]
            elif not ln.startswith("* ") and ln and ln[0].islower():
                ln = ln[0].upper() + ln[1:]
            if ln:
                lines.append(ln)
        return stash.restore("\n".join(lines).strip())

    # 11. Capitalise "I" / line starts (skip code), fix question & sentence punctuation
    out = _finalize_lines(out, apply_questions, continuation)

    # 12. Output style (rules fallback)
    out = apply_style(out, style)

    # Clean redundant spaces & punctuation (newline-aware)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r" ?\n ?", "\n", out)
    out = re.sub(r"[ \t]+([,.?!])", r"\1", out)
    out = re.sub(r"([,.?!]){2,}", r"\1", out)

    restored = stash.restore(out).strip()
    # Placeholders (dictionary/email) suppress the final period in
    # _finalize_lines; re-apply it now that the real words are back.
    # Skips code lines (no trailing period) and multi-line/bullet output.
    if (restored and apply_questions and "\n" not in restored
            and not restored.startswith("* ") and not _is_code_line(restored)
            and not restored.endswith((".", "!", "?", ":", ";", '"', "\u201d"))):
        restored = fix_question_punctuation(restored, terminal_period=True)
    return restored


def enhance_text(text: str, apply_slang: bool = True, apply_questions: bool = True,
                 style: str = "default", prev_text: str = "") -> str:
    """
    Main enhancement entrypoint (Approach 1: Instant, 0 VRAM, stdlib + re only).
    style: default | formal | casual | concise | bullets
    prev_text: previous chunk; if it ended mid-sentence a duplicated 1-3 word
               prefix of `text` is dropped.
    """
    if not text or not text.strip():
        return ""
    out, continuation = _apply_prev_text(text.strip(), prev_text)
    return _enhance_core(out, apply_slang, apply_questions, _normalize_style(style), continuation)


# ---------------------------------------------------------------------------
# Local LLM (Qwen 2.5 0.5B) management
# ---------------------------------------------------------------------------
def is_llm_downloaded() -> bool:
    """Check if Qwen 2.5 0.5B model weights are fully downloaded."""
    target = os.path.join(MODEL_DIR, "model.safetensors")
    if os.path.exists(target):
        try:
            return os.path.getsize(target) >= 900 * 1024 * 1024
        except Exception:
            return False
    return False


@functools.lru_cache(maxsize=1)
def _check_torch_cuda() -> bool:
    """Check if PyTorch has CUDA available, cached at module level to avoid ~4s import stall."""
    try:
        if "torch" in sys.modules:
            import torch
            return bool(torch.cuda.is_available())
        # Fast path: inspect torch version without triggering heavy ~4s C-extension load
        import importlib.util
        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            vfile = os.path.join(os.path.dirname(spec.origin), "version.py")
            if os.path.exists(vfile):
                with open(vfile, "r", encoding="utf-8") as f:
                    vcontent = f.read()
                if "cuda = None" in vcontent or "cuda: Optional[str] = None" in vcontent:
                    return False
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


_is_torch_cuda_available = _check_torch_cuda


def is_llm_ready_for_gpu() -> bool:
    """Check if Qwen model is ready with GPU acceleration (CTranslate2 or PyTorch CUDA)."""
    if os.path.exists(os.path.join(CT2_DIR, "model.bin")):
        return True
    if is_llm_downloaded():
        return _check_torch_cuda()
    return False


def get_llm_status() -> dict:
    """Return download status and file size for UI."""
    global _is_downloading, _download_progress
    downloaded = is_llm_downloaded()
    ready = is_llm_ready_for_gpu()
    target = os.path.join(MODEL_DIR, "model.safetensors")
    size_mb = 0
    if os.path.exists(target):
        try:
            size_mb = round(os.path.getsize(target) / (1024 * 1024), 1)
        except Exception:
            pass
    return {
        "downloaded": downloaded,
        "ready": ready,
        "downloading": _is_downloading,
        "size_mb": size_mb,
        "total_mb": 942.3,
        "progress": _download_progress
    }


def start_llm_download() -> bool:
    """Start background download of Qwen 2.5 0.5B weights if not present."""
    global _is_downloading, _download_progress
    with _download_lock:
        if _is_downloading or is_llm_downloaded():
            return True
        _is_downloading = True
        _download_progress = "Starting download..."

    def _worker():
        global _is_downloading, _download_progress
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            url = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/resolve/main/model.safetensors"
            target = os.path.join(MODEL_DIR, "model.safetensors")
            _download_progress = "Starting..."
            cmd = ["curl.exe", "-L", "-C", "-", "--progress-bar", "-o", target, url]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, text=True, bufsize=1,
                                    creationflags=flags)
            pct_re = re.compile(r"(\d+(?:\.\d+)?)%")
            buf = ""
            while True:
                ch = proc.stderr.read(1)
                if not ch:
                    break
                if ch == "\r" or ch == "\n":
                    found = pct_re.findall(buf)
                    if found:
                        try:
                            _download_progress = f"{float(found[-1]):.0f}%"
                        except Exception:
                            pass
                    buf = ""
                else:
                    buf += ch
                    if len(buf) > 160:
                        buf = buf[-160:]
            try:
                proc.stderr.close()
            except Exception:
                pass
            proc.wait()
            if proc.returncode == 0 and os.path.exists(target):
                _download_progress = "Download complete!"
            else:
                _download_progress = f"Download failed (exit {proc.returncode})"
        except Exception as e:
            _download_progress = f"Download failed: {e}"
        finally:
            _is_downloading = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return True


def delete_llm_files() -> dict:
    """Delete the ~2GB Qwen binaries (model.safetensors + CT2 model.bin).

    Configs/tokenizers stay so a re-download restores exactly the two files.
    Refuses while a download is in flight.
    """
    global _llm_model, _llm_tokenizer
    if _is_downloading:
        st = get_llm_status()
        st["ok"] = False
        st["reason"] = "downloading"
        return st
    removed = 0
    for p in (os.path.join(MODEL_DIR, "model.safetensors"),
              os.path.join(CT2_DIR, "model.bin")):
        try:
            if os.path.exists(p):
                os.remove(p)
                removed += 1
        except Exception:
            pass
    with _llm_lock:
        _llm_model = None
        _llm_tokenizer = None
    st = get_llm_status()
    st["ok"] = True
    st["removed"] = removed
    return st


def load_llm_model():
    """Lazy-load Qwen 0.5B model and tokenizer into memory once."""
    global _llm_model, _llm_tokenizer
    with _llm_lock:
        if _llm_model is not None and _llm_tokenizer is not None:
            return _llm_model, _llm_tokenizer

        # 1. Prefer CTranslate2 on GPU if available (10x faster, zero PyTorch thread overhead!)
        if os.path.exists(os.path.join(CT2_DIR, "model.bin")):
            try:
                import ctranslate2
                from transformers import AutoTokenizer
                from transcriber import setup_cuda_dlls
                setup_cuda_dlls()

                has_cuda = ctranslate2.get_cuda_device_count() > 0
                device = "cuda" if has_cuda else "cpu"
                compute_type = "float16" if has_cuda else "int8"
                print(f"[SmartEnhancer] Loading Qwen 0.5B on CTranslate2 ({device}, {compute_type})...")
                _llm_tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
                _llm_model = ctranslate2.Generator(CT2_DIR, device=device, compute_type=compute_type)
                print(f"[SmartEnhancer] Qwen 0.5B (CTranslate2 {device}) ready!")
                return _llm_model, _llm_tokenizer
            except Exception as e:
                print(f"[SmartEnhancer] CT2 load failed ({e}), checking PyTorch fallback...")

        # 2. Fallback to Transformers safetensors
        if not is_llm_downloaded():
            return None, None

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            threads = max(1, min(4, (os.cpu_count() or 4) // 2))
            torch.set_num_threads(threads)

            has_cuda = torch.cuda.is_available()
            if not has_cuda:
                print("[SmartEnhancer] Notice: PyTorch CUDA is not available. Instant Fast rules mode is recommended for real-time dictation.")
                return None, None

            print("[SmartEnhancer] Loading Qwen 2.5 0.5B PyTorch on CUDA...")
            _llm_tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
            _llm_model = AutoModelForCausalLM.from_pretrained(
                MODEL_DIR,
                torch_dtype=torch.float16,
                low_cpu_mem_usage=True
            ).to("cuda")
            _llm_model.eval()
            print("[SmartEnhancer] Qwen 0.5B PyTorch ready!")
            return _llm_model, _llm_tokenizer
        except Exception as e:
            print(f"[SmartEnhancer] Error loading Qwen 0.5B: {e}")
            return None, None


_LLM_BASE_PROMPT = (
    "You are a verbatim speech transcription formatter for a text editor. Your ONLY job is to format the speaker's words.\n"
    "CRITICAL INSTRUCTIONS:\n"
    "- YOU MUST NEVER ANSWER QUESTIONS. If the speaker asks 'who is someone' or 'what is something', output their exact question.\n"
    "- If the speaker asks a question, preserve it as a question ending with '?'. Do NOT write the answer.\n"
    "- Drop vocal fillers (um, uh, ah, er, hmm, like, you know, basically, actually, okay so) and false starts.\n"
    "- Technical terms stay conventional: 'dot env' -> .env, 'package dot json' -> package.json.\n"
    "- Fix capitalization, grammar, and punctuation. Output ONLY the formatted speech."
)
_STYLE_PROMPTS = {
    "default": "",
    "formal": ("\n- STYLE: Formal and professional. Expand contractions (don't -> do not), "
               "replace slang with standard wording, use complete sentences."),
    "casual": ("\n- STYLE: Casual and conversational. Keep contractions and a relaxed tone. "
               "No trailing period on a single short sentence."),
    "concise": ("\n- STYLE: Concise. Remove hedging, repetition and filler phrases. "
                "Keep every fact, name and number. Use the shortest clear wording."),
    "bullets": ("\n- STYLE: Output a markdown bullet list, one idea per line, every line starting "
                "with '* '. Do not add any new information."),
}
_STYLE_EXAMPLES = {
    "formal": ("Dictation: hey can you send me the report cuz i cant find it",
               "Hello, could you please send me the report? I cannot find it."),
    "casual": ("Dictation: I will not be able to attend the meeting tomorrow",
               "I won't be able to make the meeting tomorrow"),
    "concise": ("Dictation: i was just wondering if you could possibly send me the file when you get a chance",
                "Could you send me the file when you can?"),
    "bullets": ("Dictation: buy milk then call mom and book the flight",
                "* Buy milk\n* Call mom\n* Book the flight"),
}


def _post_llm(generated: str, style: str) -> str:
    """Deterministic post-pass on model output: snippets, dictionary, entities, bullets."""
    st = _Stash()
    out = apply_snippets(generated, st)
    out = apply_custom_replacements(out, st)
    out = normalize_entities(out, st)
    if style == "bullets":
        out = re.sub(r"(?m)^\s*(?:[-\u2022*]\s+)", "* ", out)
        if "* " not in out:
            out = _to_bullets(out)
    return st.restore(out).strip()


def enhance_with_llm(text: str, style: str = "default", prev_text: str = "") -> str:
    """
    Neural polish pass using local Qwen 2.5 (0.5B).
    Fixes grammar, drops conversational fillers, handles lists, and preserves original meaning.
    Falls back to the rules pipeline whenever the model is unavailable.
    """
    if not text or not text.strip():
        return ""

    style = _normalize_style(style)
    cleaned_input, continuation = _apply_prev_text(text.strip(), prev_text)
    words = cleaned_input.split()

    # For 1-2 words ("yes", "okay", "thank you"), skip LLM overhead and use fast rules
    if len(words) <= 2:
        return _enhance_core(cleaned_input, True, True, style, continuation)

    cleaned_input = fix_repeats(cleaned_input)

    # Explicit formatting commands (new line, bullet point, comma ...) are deterministic:
    # the 0.5B model would only get them wrong, so the rules pipeline handles them.
    if apply_spoken_punctuation(cleaned_input) != cleaned_input:
        return _enhance_core(cleaned_input, True, True, style, continuation)

    # First clean basic self-corrections so the model gets clean context
    cleaned_input = fix_self_corrections(cleaned_input)

    model, tokenizer = load_llm_model()
    if model is None or tokenizer is None:
        return _enhance_core(cleaned_input, True, True, style, continuation)

    try:
        import torch

        system_instruction = _LLM_BASE_PROMPT + _STYLE_PROMPTS[style]

        if style in _STYLE_EXAMPLES:
            ex_user, ex_assistant = _STYLE_EXAMPLES[style]
        else:
            ex_user, ex_assistant = "Dictation: meet me at five no wait six pm", "Meet me at 6 PM."
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": ex_user},
            {"role": "assistant", "content": ex_assistant},
            {"role": "user", "content": "Dictation: what time does the morning train arrive"},
            {"role": "assistant", "content": "What time does the morning train arrive?"},
            {"role": "user", "content": f"Dictation: {cleaned_input}"}
        ]

        chat_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        extra = 45 if style in ("bullets", "formal") else 25

        if hasattr(model, "generate_batch"):
            # CTranslate2 Generator (Ultra-fast CUDA path, ~100ms)
            prompt_tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(chat_text))
            max_len = len(prompt_tokens) + min(len(words) * 3 + extra, 140)
            results = model.generate_batch(
                [prompt_tokens],
                max_length=max_len,
                sampling_topk=1,
                end_token=[tokenizer.eos_token_id]
            )
            output_tokens = results[0].sequences_ids[0][len(prompt_tokens):]
            generated = tokenizer.decode(output_tokens).strip()
        else:
            # PyTorch fallback
            import torch
            inputs = tokenizer([chat_text], return_tensors="pt")
            max_tokens = min(len(words) * 3 + extra + 5, 160)
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id
                )
            generated = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True).strip()

        generated = re.sub(r"^Dictation:\s*", "", generated, flags=re.IGNORECASE).strip()
        generated = re.sub(r"</?transcript>", "", generated, flags=re.IGNORECASE).strip()

        if not generated or len(generated) < 2:
            return _enhance_core(cleaned_input, True, True, style, continuation)

        return _post_llm(generated, style)
    except Exception as e:
        print(f"[SmartEnhancer] LLM inference failed: {e}. Falling back to rules.")
        return _enhance_core(cleaned_input, True, True, style, continuation)


def enhance_text_dispatch(text: str, mode: str = "rules", style: str = "default",
                          prev_text: str = "") -> str:
    """
    Dispatches text enhancement based on selected user preference.
      mode:      rules (default) | llm | off
      style:     default | formal | casual | concise | bullets
      prev_text: previous chunk, for context continuation (duplicate-prefix drop)
    Existing callers (text, mode=...) keep working unchanged.
    """
    if mode == "off":
        return text.strip()
    elif mode == "llm":
        return enhance_with_llm(text, style=style, prev_text=prev_text)
    else:  # "rules" (default)
        return enhance_text(text, style=style, prev_text=prev_text)


# ---------------------------------------------------------------------------
# Self-test: 30 before/after cases (run: python smart_enhancer.py)
# ---------------------------------------------------------------------------
def _run_self_tests() -> int:
    import tempfile

    try:  # exercise the real app pipeline when cleaner.py is next to us
        import cleaner
        _clean = cleaner.clean_text
    except Exception:
        _clean = lambda s: s  # noqa: E731

    tmp = tempfile.mkdtemp(prefix="quotal_enh_")
    dict_path = os.path.join(tmp, "dictionary.json")
    snip_path = os.path.join(tmp, "snippets.json")
    with open(dict_path, "w", encoding="utf-8") as f:
        json.dump({"quotal": "Quotal", "kube ctl": "kubectl"}, f)
    with open(snip_path, "w", encoding="utf-8") as f:
        json.dump({"my email": "dev@quotal.app", "my address": "12 Marine Drive, Mumbai"}, f)

    global DICTIONARY_FILE, SNIPPETS_FILE
    saved = (DICTIONARY_FILE, SNIPPETS_FILE)
    DICTIONARY_FILE, SNIPPETS_FILE = dict_path, snip_path
    reload_user_data()

    def pipe(raw, **kw):
        # same order as app.py: cleaner -> enhancer
        return enhance_text(_clean(raw), **kw)

    # (group, input, kwargs, expected)
    T = [
        # --- regression: Hinglish / hum / err ---
        ("hinglish", "aaj milte hai kya", {}, "Aaj milte hai kya?"),
        ("hinglish", "aaj milte hai, hum, theek hai", {}, "Aaj milte hai, hum, theek hai."),
        ("hum kept", "hum log aaj jayenge.", {}, "Hum log aaj jayenge."),
        ("err kept", "err I think that's wrong.", {}, "Err I think that's wrong."),
        ("hesitation", "um so aaj milte hai, okay?", {}, "So aaj milte hai, okay?"),
        ("reduplication", "dheere dheere chalo", {}, "Dheere dheere chalo."),
        # --- regression: code dictation ---
        ("code", "const x equals five", {}, "const x = 5"),
        ("code", "const a equals one comma b equals two", {}, "const a = 1, b = 2"),
        ("code", "git commit minus m fix the bug", {}, "git commit - m fix the bug"),
        ("code", "docker run my app with detached flag", {}, "docker run my app -d"),
        # --- regression: no wait / corrections / false positives ---
        ("no wait", "we meet at 5, no wait, 6 pm", {}, "We meet at 6 PM."),
        ("no wait", "five, no, six chairs please", {}, "Six chairs please."),
        ("no no", "I want tea no no coffee please", {}, "I want coffee please."),
        ("no idea", "i have no idea", {}, "I have no idea."),
        ("no smoking", "no smoking in the building", {}, "No smoking in the building."),
        # --- 1 spoken punctuation ---
        ("punct", "hello comma how are you question mark", {}, "Hello, how are you?"),
        ("punct", "thanks period new paragraph see you tomorrow", {}, "Thanks.\n\nSee you tomorrow."),
        ("punct", "shopping list bullet point milk bullet point eggs", {}, "Shopping list\n* Milk\n* Eggs"),
        ("punct guard", "the time period of the project is two weeks", {},
         "The time period of the project is two weeks."),
        # --- 2 repeats ---
        ("repeats", "I I I want to to go", {}, "I want to go."),
        ("repeats", "this is w-w-wonderful", {}, "This is wonderful."),
        ("repeats", "I went to the, I went to store", {}, "I went to store."),
        ("repeats guard", "very very good bye bye", {}, "Very very good bye bye."),
        # --- 3 fillers ---
        ("fillers", "so I was, like, really tired, you know, after work", {},
         "So I was really tired after work."),
        ("fillers", "I actually like it", {}, "I like it."),
        ("fillers keep", "Actually 6pm works for me", {}, "Actually 6 PM works for me."),
        # --- 4 normalization ---
        ("normalize", "call me at five thirty pm on march fifth twenty twenty six", {},
         "Call me at 5:30 PM on March 5th, 2026."),
        ("normalize", "it costs twenty five dollars", {}, "It costs $25."),
        ("normalize", "my email is john at gmail dot com", {}, "My email is john@gmail.com."),
        # --- 5 style + prev_text ---
        ("style", "i dont think i can make it", {"style": "formal"}, "I do not think I can make it."),
        ("prev_text", "at five thirty pm", {"prev_text": "we should meet at"}, "5:30 PM."),
        # --- 6 dictionary / 7 snippets ---
        ("dictionary", "i love quotal", {}, "I love Quotal."),
        ("snippet", "send it to my email please", {}, "Send it to dev@quotal.app please."),
    ]

    failed = 0
    print("--- SMART ENHANCER TESTS ---")
    for n, (group, raw, kw, expected) in enumerate(T, 1):
        got = pipe(raw, **kw)
        ok = got == expected
        failed += 0 if ok else 1
        print(f"[{n:02d}] {'PASS' if ok else 'FAIL'} ({group})")
        print(f"     BEFORE: {raw!r}")
        print(f"     AFTER : {got!r}")
        if not ok:
            print(f"     WANTED: {expected!r}")
    DICTIONARY_FILE, SNIPPETS_FILE = saved
    reload_user_data()
    print(f"\n{len(T) - failed}/{len(T)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_self_tests() else 0)
