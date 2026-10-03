"""
cleaner.py - Hesitation and filler removal ported from simple-voice (sv-text).

Deterministically cleans speech transcripts before pasting:
- Strips filler sounds: "um", "uh", "hmm", "ah", "er", etc.
- Preserves real words: "err", "hum" (Hindi for "we").
- Cleans up trailing/hanging punctuation.
- Normalizes spacing and capitalizes sentences.
"""

import re

# Hesitation shapes (after lower-casing and collapsing runs of same letter)
SHAPES = {
    "um", "uhm", "uh", "ah", "ahm", "ahum", "eh", "er", "erm", "hm", "hmm"
}

# Real words that shouldn't be stripped
KEPT_WORDS = {"err", "hum"}

SENTENCE_END_CHARS = {'.', '?', '!'}
TRAILING_PUNCT = {',', '.', ';', ':', '?', '!', '-', '…'}


def collapse_repeated_letters(word: str) -> str:
    """Collapses consecutive repeated letters (e.g. 'ummmm' -> 'um', 'uhhhh' -> 'uh')."""
    if not word:
        return ""
    result = []
    for char in word.lower():
        if not result or result[-1] != char:
            result.append(char)
        elif len(result) >= 2 and result[-1] == char and result[-2] == char:
            continue
        else:
            result.append(char)
    # Simplify further to pure run collapse for hesitation matching
    collapsed = re.sub(r'(.)\1+', r'\1', word.lower())
    return collapsed


def is_hesitation(word: str) -> bool:
    """Check if a word is a hesitation sound."""
    core_word = word.strip().lower()
    if not core_word:
        return False
    if core_word in KEPT_WORDS:
        return False
    if core_word in SHAPES:
        return True
    
    # Check collapsed form (e.g. 'ummm' -> 'um')
    collapsed = collapse_repeated_letters(core_word)
    if collapsed in SHAPES and core_word not in KEPT_WORDS:
        return True
        
    # Check hyphenated hesitations (e.g. 'uh-um', 'e-au')
    if '-' in core_word:
        parts = core_word.split('-')
        return all(p in SHAPES or collapse_repeated_letters(p) in SHAPES for p in parts if p)
        
    return False


def strip_hesitations(text: str) -> str:
    """
    Strips hesitation tokens with attached punctuation.
    If a hesitation token at the end of a sentence carries terminal punctuation,
    transfers the punctuation to the previous word.
    """
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        words = line.split()
        if not words:
            continue
            
        kept_words = []
        for i, word in enumerate(words):
            # Extract punctuation attached to the word
            core = word.rstrip(',.;:?!-…')
            trailing = word[len(core):]
            
            if is_hesitation(core):
                # If this hesitation had a sentence-ending mark and there's a previous word,
                # attach the sentence-ending mark to the previous word.
                if kept_words and any(c in SENTENCE_END_CHARS for c in trailing):
                    end_char = next((c for c in reversed(trailing) if c in SENTENCE_END_CHARS), '.')
                    kept_words[-1] = kept_words[-1].rstrip(',;:-') + end_char
                continue
                
            kept_words.append(word)
            
        if kept_words:
            cleaned_lines.append(" ".join(kept_words))
            
    return "\n".join(cleaned_lines)


def clean_text(text: str) -> str:
    """
    Main clean pipeline:
    1. Strip hesitations (um, uh, etc.)
    2. Normalize whitespace
    3. Capitalize first letter of sentences
    4. Clean dangling commas/spaces
    """
    if not text or not text.strip():
        return ""
        
    # Strip hesitations
    cleaned = strip_hesitations(text)
    
    # Normalize spaces
    cleaned = re.sub(r'[ \t]+', ' ', cleaned).strip()
    
    # Remove leading punctuation (like hanging commas or periods)
    cleaned = re.sub(r'^[,\s;:-]+', '', cleaned)
    
    # Capitalize first character if it's an ASCII letter
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
        
    # Capitalize after sentence ends (. ? !)
    def cap_after_period(match):
        return match.group(1) + match.group(2).upper()
        
    cleaned = re.sub(r'([.?!]\s+)([a-z])', cap_after_period, cleaned)
    
    return cleaned.strip()


if __name__ == "__main__":
    # Test cases
    samples = [
        "um so aaj milte hai, okay?",
        "uhh kal meeting hai, um wait, parso hai.",
        "haan ummm theek hai, uh let's go.",
        "hum log aaj jayenge.",  # "hum" must stay!
        "err I think that's wrong.",  # "err" must stay!
    ]
    for s in samples:
        print(f"RAW:   {s}")
        print(f"CLEAN: {clean_text(s)}\n")
