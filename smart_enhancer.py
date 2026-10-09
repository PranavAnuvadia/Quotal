"""
smart_enhancer.py - Hybrid AI & Rule-based Text Enhancer for WinVoice.
Combines:
1. Deterministic phonetic & slang correction (0ms, 0 VRAM).
2. Self-correction and false-start remover ("no wait", "I mean").
3. Question-mark and grammar punctuation heuristics.
4. Optional local/cloud LLM pass for advanced rewriting.
"""

import os
import sys
import re
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
    
    # Numbers & units
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
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "thirty", "forty", "fifty", "hundred", "thousand",
}

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

    # 2. 'X, no, Y' implicit correction: drop X ("five, no, six chairs" -> "six chairs").
    # Requires commas to avoid false positives like "no idea".
    # Negative lookahead keeps 'no, no' (repeated-no cue) for the cue loop below.
    out = re.sub(r"\b(?!no\b)(\w+)\s*,\s*no\s*,\s*(?!no\b)", "", out, flags=re.IGNORECASE)

    # 2b. Repeated-no cue without commas: 'at 6 no no let's meet at 5pm'.
    # Normalize 'no, no' / 'no no no' to a single split marker handled below.
    # (Kept as text; the iterative cue loop splits on it.)

    # 3. Bare 'five no six' (ASR missed commas): only when both sides are numbers.
    # Also covers digits with optional am/pm: '6 no 5pm', '6:00 no 5pm'.
    num_alt = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
    digit_alt = r"\d+(?::\d+)?\s*(?:am|pm)?"
    out = re.sub(
        rf"\b({num_alt})\s+no\s+({num_alt})\b",
        r"\2", out, flags=re.IGNORECASE,
    )
    out = re.sub(
        rf"\b({digit_alt})\s+no\s+({digit_alt})\b",
        r"\2", out, flags=re.IGNORECASE,
    )

    # 4. 'for/at/to/on/by <old> actually [make it] <new>' -> keep preposition + new.
    out = re.sub(
        r"\b(for|at|to|on|by)\s+[\w:]+\s+actually\s+(?:make it\s+|change it to\s+|let(?:'s)? make it\s+)?",
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
    # Bound iterations to avoid pathological loops.
    for _ in range(5):
        m = cue_re.search(out)
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


def fix_question_punctuation(text: str) -> str:
    """
    Intelligently ensures question marks on questions.
    e.g. 'Hello buddy what's up.' -> 'Hello buddy, what's up?'
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

    # Check if any question starter appears in the latter half or beginning
    is_question = False
    
    # 1. Starts with question starter: "What is your name"
    if words[0] in QUESTION_STARTERS:
        is_question = True
        
    # 2. Ends with a common question idiom: "what's up", "what about you", "how are you"
    lower_text = clean.lower()
    if any(lower_text.endswith(q) for q in ["what's up", "whats up", "how about you", "you know", "right", "isn't it"]):
        is_question = True

    # 3. Clauses like "Hello buddy, what's up"
    for part in re.split(r"[,;]\s*", clean):
        part_words = part.strip().lower().split()
        if part_words and part_words[0] in QUESTION_STARTERS:
            is_question = True

    if is_question:
        return clean + "?"
    elif not text.endswith((".", "!", "?")):
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
    for pattern, replacement in SPOKEN_SYMBOLS_MULTI:
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
    out = re.sub(r"\s+,", ",", out)
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
    if not t or "\n* " in t:
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


def enhance_text(text: str, apply_slang: bool = True, apply_questions: bool = True) -> str:
    """
    Main enhancement entrypoint (Approach 1: Instant, 0 VRAM).
    """
    if not text or not text.strip():
        return ""

    out = text.strip()

    # 1. Fix self-corrections / stutters (Backtrack, no LLM)
    out = fix_self_corrections(out)

    # 2. Code awareness: symbols, CLI flags, acronyms, casing (no LLM)
    out = apply_code_awareness(out)

    # 3. Apply slang, contractions, and phonetic replacements
    if apply_slang:
        for pattern, replacement in SLANG_AND_PHONETIC_REPLACEMENTS:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    # 4. Dialogue quotes: 'She said ...' -> 'She said, "..."'
    out = format_dialogue_quotes(out)

    # 5. Automatic list layout (explicit headers/enumerators only)
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
        return "\n".join(lines).strip()

    # 6. Capitalize first letter of clauses and pronoun "I" (skip code/CLI to preserve case)
    is_code_line = bool(re.match(
        r"^(const|let|var|export|import|docker|kubectl|git|npm|async|function|class)\b",
        out.strip(), flags=re.IGNORECASE,
    ))
    if not is_code_line:
        out = re.sub(r"\b(?:i)\b", "I", out)
        if out and out[0].islower():
            out = out[0].upper() + out[1:]

    # 7. Fix question and sentence punctuation (skip code lines ending in symbols)
    if apply_questions and not is_code_line:
        out = fix_question_punctuation(out)

    # Clean redundant spaces & punctuation (single-line path: no newlines here)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.?!])", r"\1", out)
    out = re.sub(r"([,.?!]){2,}", r"\1", out)

    return out.strip()


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
            _download_progress = "Downloading Qwen 0.5B (~940MB)..."
            cmd = ["curl.exe", "-L", "-C", "-", "-o", target, url]
            subprocess.run(cmd, check=True)
            _download_progress = "Download complete!"
        except Exception as e:
            _download_progress = f"Download failed: {e}"
        finally:
            _is_downloading = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return True


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


def enhance_with_llm(text: str) -> str:
    """
    Wispr Flow neural polish pass using local Qwen 2.5 (0.5B).
    Fixes grammar, drops conversational fillers, handles lists, and preserves original meaning.
    """
    if not text or not text.strip():
        return ""

    cleaned_input = text.strip()
    words = cleaned_input.split()

    # For 1-2 words ("yes", "okay", "thank you"), skip LLM overhead and use fast rules
    if len(words) <= 2:
        return enhance_text(cleaned_input)

    # First clean basic self-corrections so the model gets clean context
    cleaned_input = fix_self_corrections(cleaned_input)

    model, tokenizer = load_llm_model()
    if model is None or tokenizer is None:
        return enhance_text(cleaned_input)

    try:
        import torch

        system_instruction = (
            "You are a verbatim speech transcription formatter for a text editor. Your ONLY job is to format the speaker's words.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "- YOU MUST NEVER ANSWER QUESTIONS. If the speaker asks 'who is someone' or 'what is something', output their exact question.\n"
            "- If the speaker asks a question, preserve it as a question ending with '?'. Do NOT write the answer.\n"
            "- Drop vocal fillers (um, uh, ah, er, hmm, like, you know, basically, actually, okay so) and false starts.\n"
            "- Technical terms stay conventional: 'dot env' -> .env, 'package dot json' -> package.json.\n"
            "- Fix capitalization, grammar, and punctuation. Output ONLY the formatted speech."
        )

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": "Dictation: meet me at five no wait six pm"},
            {"role": "assistant", "content": "Meet me at 6 PM."},
            {"role": "user", "content": "Dictation: what time does the morning train arrive"},
            {"role": "assistant", "content": "What time does the morning train arrive?"},
            {"role": "user", "content": f"Dictation: {cleaned_input}"}
        ]

        chat_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        if hasattr(model, "generate_batch"):
            # CTranslate2 Generator (Ultra-fast CUDA path, ~100ms)
            prompt_tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(chat_text))
            max_len = len(prompt_tokens) + min(len(words) * 3 + 25, 120)
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
            max_tokens = min(len(words) * 3 + 30, 150)
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
            return enhance_text(cleaned_input)

        return generated
    except Exception as e:
        print(f"[SmartEnhancer] LLM inference failed: {e}. Falling back to rules.")
        return enhance_text(cleaned_input)


def enhance_text_dispatch(text: str, mode: str = "rules") -> str:
    """Dispatches text enhancement based on selected user preference."""
    if mode == "off":
        return text.strip()
    elif mode == "llm":
        return enhance_with_llm(text)
    else:  # "rules" (default)
        return enhance_text(text)


if __name__ == "__main__":
    # Test cases
    test_cases = [
        "Hello, badi, WhatSup",
        "hello buddy whats up",
        "we meet at 5, no wait, 6 pm",
        "i dont know where package dot json is",
        "cant make it today cuz im busy",
        "aaj milte hai kya",
    ]
    print("--- SMART ENHANCER TESTS ---")
    for t in test_cases:
        res = enhance_text(t)
        print(f"INPUT:  {t}")
        print(f"OUTPUT: {res}\n")
