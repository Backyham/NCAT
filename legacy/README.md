# legacy/

Superseded scripts, kept for reference. **Do not use these for new work.**

Everything here has been replaced by the `pipeline/` package, which is the
supported implementation. The originals are retained because a few
non-obvious pieces of logic were extracted from them and the reasoning is
worth being able to check against the source:

| File | Superseded by | What was taken from it |
|---|---|---|
| `Full-auto_M134A16.py` | `pipeline/segment.py`, `pipeline/style.py` | The greedy-LCS + LIS word alignment, and the `AegiLine` ASS serialisation. Its comments explain why the textbook LCS backtrack fails on stuttered ASR output — that reasoning was not in any paper and is easy to lose. |
| `download.py` | `pipeline/download.py` | yt-dlp option construction |
| `transcode.py` | `pipeline/transcode.py` | 720p proxy + 16 kHz mono WAV recipe, encoder preference order |
| `transcribe.py` | `pipeline/transcribe.py` | ASR dispatch and VoiceBase-style JSON output shape |
| `asr_qwen.py` | `pipeline/transcribe.py` | The Qwen3-ASR + ForcedAligner integration |
| `sub_renderer.py` | `pipeline/render.py` | FFmpeg `ass` filter invocation for burn-in |
| `nltk_func.py`, `file_in.py`, `exception_proc.py` | — | Small helpers, replaced inline |

## Differences from the replacements

The current pipeline differs from these in ways that were deliberate, not
accidental:

- **No module-level `input()`.** `sub_renderer.py` prompted twice before
  doing any work, which deadlocks a web request handler. The interactive
  pause now lives only in the CLI entry point.
- **Paths are explicit.** These scripts wrote artifacts into whatever
  directory the user happened to be standing in. `pipeline/paths.py`
  concentrates that into a `JobPaths` per job.
- **Report callbacks instead of prints.** Stages report progress through a
  callable so the web layer can stream it.
- **Separate venvs.** `pipeline/` has no torch dependency; ASR runs in
  `.venv-asr`.

## Running them

They still import each other, so they must be run from inside this
directory:

```bash
python legacy/transcribe.py        # will fail: needs nltk
```

Dependencies are not declared for the legacy tree. Installing them to
actually run these scripts is not recommended — use `pipeline/` instead.

## Origin

`Full-auto_M134A16.py` is Kilo19's original Nixiesubs tool (matching
algorithm by davidzz). The rest are this project's earlier iterations.
