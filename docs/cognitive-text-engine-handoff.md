# Quotal cognitive text engine handoff

## Changes made

| File | Changes |
| --- | --- |
| `processing/smart_enhancer.py` | Kept the existing rules pipeline and public entry point. Added `app_context`, Hybrid routing, a 1–3 word fast path for ordinary dictation, protected code handling, explicit time revisions, and fallback formatting for email, bullets, action items, and summary. Added a single background inference worker, model warm-up, a 230 ms normal wait budget, and a four-second batch budget for long inputs and semantic commands. Fixed the model paths to use Quotal's app directory. CTranslate2 now decodes completion tokens only, caches the fixed prompt prefix, and rejects unfinished or late results. The local model can be overridden with `QUOTAL_COGNITIVE_MODEL`. The dashboard model download now requests the pinned Qwen weights and tokenizer files together. |
| `processing/cognitive_text.py` | Added classification for explicit leading or trailing summary, email, translation, and bullet directives, plus leading action items. Added process and browser-title profiles, the exact system prompt and few-shot examples, literal protection, and validation for malformed output, lost placeholders, some numeric changes, and summaries that drop signals of unresolved issues. |
| `processing/foreground_context.py` | Added Win32 foreground window capture for executable name and window title. The model receives the derived destination category. |
| `app.py` | Captures the foreground application before the recording overlay appears, carries it into the dictation worker, and passes the raw ASR transcript and selected style to the enhancer after the existing language policy. Warms the model in the background and logs enhancement time separately from ASR. |
| `core/settings_manager.py`, `ui/dashboard/dashboard.html`, `ui/dashboard/dashboard_host.py` | Added the writing-style preference, Hybrid mode, a six-style selector, and updated local model status messages. Existing mode selections are retained. |
| `tests/test_cognitive_enhancer.py` | Added 16 tests covering the supplied examples with controlled model responses, voice commands, destination routing, style and mode selection, dictionary and snippet protection, unsupported transformations, deadlines and busy-worker fallback, CTranslate2 decoding and caching, prompt delimiters, and the model download contract. Added an opt-in live model benchmark. |
| `docs/cognitive-text-engine.md` | Documented architecture, routing, voice commands, model setup, limits, and benchmark results. |

The worktree already contained Quotal's package reorganization and other edits when this work began. Many files appeared as added or deleted in Git because of that reorganization. The table records the changes from this task.

## Verification performed

| Check | Result |
| --- | --- |
| `python -m processing.smart_enhancer` | All 33 legacy rule cases passed. |
| `python -m unittest discover -s tests -v` | All 16 new tests passed. The tests use controlled model responses and mock the model download. |
| `python -m compileall -q app.py core/settings_manager.py processing ui/dashboard/dashboard_host.py tests` | Passed. |
| Dashboard JavaScript parsed with Node's `vm.Script` | Passed. |
| Windows foreground-context API smoke check | Returned the active window title. The process name was unavailable for that window in the test environment. |
| `QuotalApp._process_and_paste` smoke check with simulated ASR, history, and paste | Passed for Slack conversational formatting, an intact Windows Terminal command with `--save-dev`, and a bulleted document dictation. A physical hotkey, microphone, and real paste were outside this check. |
| `git diff --check` | Passed. Git printed line-ending conversion notices on existing working-copy files. |
| `./.venv/Scripts/python.exe -u -m tests.test_cognitive_enhancer --live` | Ran on an RTX 3050 with Qwen 2.5 0.5B through CTranslate2 CUDA. Five of nine checks passed. The benchmark returned exit code 1 for four misses. |

## Live model results

- Passed the explicit time revision, bullets, email draft, action items, and French translation.
- Missed the lyric-like ASR repair, the tutorial's “movies column” → “marks column” repair, the Doomsday rambling rewrite, and the semantic summary.
- The summary guard discarded the generated result when it omitted the pending venue. The rules fallback returned the source facts.
- Enhancement-only timings in the latest run were 4.0 ms p95 for micro-dictation and 237–241 ms for the tested normal requests. Background model warm-up took 31.5 seconds.

## Open items

- The installed 0.5B model still falls short of the requested accuracy. The same live benchmark can evaluate a stronger local CTranslate2 model by setting `QUOTAL_COGNITIVE_MODEL` to a directory with converted weights and the matching Transformers tokenizer and chat template.
- The current Windows installer excludes PyTorch and the Qwen model directories. An installed copy needs a locally supplied CTranslate2 model for neural editing. The dashboard download fetches the official Transformers checkpoint and tokenizer. The download contract was tested with a mock.

See [the architecture and setup guide](cognitive-text-engine.md) and [the exact prompts and few-shot examples](../processing/cognitive_text.py).
