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


def fix_self_corrections(text: str) -> str:
    """
    Handles speech stutters and self-corrections.
    e.g. 'let's meet at 5, no wait, 6 pm' -> 'let's meet at 6 pm'
         'send it to John, I mean, David' -> 'send it to David'
    """
    # Pattern: [word/phrase] + [no wait | sorry | I mean | actually] + [replacement]
    correction_patterns = [
        r",?\s*(?:no wait|sorry|i mean|actually)\s*,?\s*",
    ]
    for pat in correction_patterns:
        # If user explicitly said "no wait Friday", keep only what comes after "no wait"
        parts = re.split(pat, text, flags=re.IGNORECASE)
        if len(parts) > 1:
            # We stitch with the latest correction
            text = parts[-1].strip()
            # If the correction is just a word or continuation, prepend the prefix before the mistake
            prefix = parts[0].strip()
            words_prefix = prefix.split()
            words_repl = text.split()
            # Keep prefix up to the last 1-2 words before the correction trigger
            if len(words_prefix) > len(words_repl):
                kept_prefix = " ".join(words_prefix[:-len(words_repl)])
                text = f"{kept_prefix} {text}".strip()
    return text


def fix_question_punctuation(text: str) -> str:
    """
    Intelligently ensures question marks on questions.
    e.g. 'Hello buddy what's up.' -> 'Hello buddy, what's up?'
    """
    if not text:
        return ""

    text = text.strip()
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


def enhance_text(text: str, apply_slang: bool = True, apply_questions: bool = True) -> str:
    """
    Main enhancement entrypoint (Approach 1: Instant, 0 VRAM).
    """
    if not text or not text.strip():
        return ""

    out = text.strip()

    # 1. Fix self-corrections / stutters
    out = fix_self_corrections(out)

    # 2. Apply slang, contractions, and phonetic replacements
    if apply_slang:
        for pattern, replacement in SLANG_AND_PHONETIC_REPLACEMENTS:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)

    # 3. Capitalize first letter of clauses and pronoun "I"
    out = re.sub(r"\b(?:i)\b", "I", out)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]

    # 4. Fix question and sentence punctuation
    if apply_questions:
        out = fix_question_punctuation(out)

    # Clean redundant spaces & punctuation
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
