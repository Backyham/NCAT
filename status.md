# NCAT — Status and Plan

Single document for where the project is and where it goes. Supersedes the
two session summaries (2026-10-16 pipeline review, 2026-10-20 Tesla P40
bring-up) and the desktop-editor TODO, all of which have been removed.

For usage and configuration see `README.md`.

Last revised: 2026-10-21.

---

## 1. Where the project stands

The **pipeline exists and is tested as far as synthetic data allows.** The
**editor does not.** NCAT was redefined this session as a self-hosted web
service; the machine was rebuilt from scratch around a Tesla P40; ASR moved
from Parakeet to Qwen3-ASR; and the workflow was restructured from flat
top-level scripts into a `pipeline/` package plus a `backend/` web service.

Nothing has yet run against a real model or real media.

---

## 2. Environment

| Component | State |
|---|---|
| OS | Arch Linux, btrfs |
| Kernel | `6.18.57-2-lts`, pinned — R580 cannot build against a rolling kernel |
| GPU | Tesla P40, `sm_61`, 24 GB, 30 SMs — `nvidia-smi` verified |
| Driver | `580.119.02` proprietary DKMS; R580 is NVIDIA's final Maxwell/Pascal/Volta branch |
| Model store | an SMB share mounted at boot; paths are per-machine, see *Configuration* |
| Shell | zsh + Oh My Zsh, `bullet-train` theme |
| venvs | `.venv-app` built; `.venv-asr` / `.venv-mt` / `.venv-llm` not built |

**Driver rationale:** patching 580 against a rolling kernel was attempted and
abandoned — three kernel APIs were removed upstream and the driver's
`ACCESS_PRIVATE()` feature detection is stale, making it unbounded
whack-a-mole. Pinning `linux-lts` lets the driver build with zero source
patches.

**Mount** — the model share is an `/etc/fstab` entry of this shape, with the
share address, mountpoint and uid/gid filled in per machine:

```
//SERVER/SHARE  /path/to/mountpoint  cifs \
  credentials=/path/to/.smb_cred,uid=UID,gid=GID,_netdev,nofail,\
  x-systemd.automount,iocharset=utf8,vers=3.0 0 0
```

`_netdev` orders it after the network; `nofail` keeps the box bootable if
the share is down; `x-systemd.automount` defers the mount to first access.
Credentials are root-owned and never read by the pipeline.

**Models on the share:**

| Model | Size | Fits 24 GB | Role |
|---|---|---|---|
| `Qwen3-ASR-1.7B-hf/` | ~4 GB | yes | in use |
| `Qwen3-ForcedAligner-0.6B-hf/` | ~1.2 GB | yes | in use |
| `Index-Translate-9B.Q8_0.gguf` | 9.8 GB | yes, all GPU | MT candidate A |
| `HY-MT2-7B-Q8_0.gguf` | 8.0 GB | yes | MT candidate B |
| `Hy-MT2-30B-A3B-Q4_K_M.gguf` | 18.2 GB | tight | unusable — custom `hy_v3` arch |
| `Qwen3.8-27B`, `Qwen3.6-27B-FP8` | ~54 GB / FP8 | no | Pascal has no FP8 tensor cores |

The two MT models are **candidates for one slot, not a primary and a
fallback**. Neither has been chosen: throughput is measured (~29 and
~17–19 tok/s) but translation quality has never been assessed, and
throughput is not a proxy for it. One will be dropped once compared.

`GET /api/models` reports which are actually present, so a missing model
surfaces before a run rather than minutes into it.

---

## 3. Design decisions

### Closed

| Question | Resolution |
|---|---|
| Desktop or web? | **Web service.** PySide6 and Tauri+Vue both considered, neither chosen. |
| Project format | **SQLite** is the source of truth; `.ass` is an export. Not `.ncatproj`. |
| Collaboration | **Dropped.** Single user. |
| Cloud SaaS, credits, presence cursors, channel auto-ingest | Out of scope. |
| ASR backend | **Qwen3-ASR + ForcedAligner.** Parakeet removed entirely. |
| MT backend | **llama.cpp GGUF.** vLLM cannot run on `sm_61` at all. |

### Still open

1. **Which MT model wins** — `Index-Translate-9B.Q8_0.gguf` and
   `HY-MT2-7B-Q8_0.gguf` are candidates competing for one slot, not a
   primary and a fallback. Throughput is measured (~29 vs ~17–19 tok/s) but
   quality has never been assessed, and throughput does not predict it. The
   translate stage therefore refuses to run without an explicit
   `--mt-model`; one entry will be deleted once the comparison is done.
2. **LLM integration** — defer indefinitely, or source a Pascal-capable
   model now? Suggestion/autocomplete has no candidate on this hardware.
3. **Mobile review** — undecided. Single user for now.
4. **`job.json` retention** — keep word-level timings after a job completes
   (enables retiming and translation memory later), or prune to save space?

### Closed

- **`legacy/Full-auto_M134A16.py` and the eight other pre-`pipeline/` scripts** —
  retained for reference, by decision. Moved to `legacy/`, which carries a
  README mapping each file to its replacement and listing the deliberate
  behavioural differences. They are never imported by the active tree.

---

## 4. Design principles

From subs.rocks and the Nixiesub workflow.

- [x] **Bilingual cue as the atomic unit** — one cue carries EN + 中文
      together; ASS export stacks them `zh\N{\r辉英}en`.
- [x] **Transparent pipeline** — seven named stages, visible progress,
      WebSocket updates, state replayed on reconnect.
- [~] **Channel-scoped memory** — glossary and term CRUD with CSV
      round-trip work. Translation memory has a table but nothing reads it.
- [~] **Live validation** — cps and line length are computed on cue write.
      No editor surface, no minimum-duration rule, not measured as you type.
- [~] **Non-destructive agent** — cue status and `needs_timing` exist; no
      agent writes suggestions yet.
- [ ] **Punch list workflow** — table exists, classifier does not.
- [x] **ASS as interchange** — CRLF + BOM verified; Aegisub opens the output.

`[~]` = partial. `[ ]` = schema or intent only.

---

## 5. Architecture

```
pipeline/            stages; each is both an importable module and a CLI
  paths.py           stage order + JobPaths (the artifact layout)
  runner.py          stage dispatch shared by CLI and web thread
  download.py  transcode.py  transcribe.py
  segment.py   translate.py  style.py     render.py

backend/
  app.py             FastAPI app, WebSocket, static hosting
  db.py              engine, session, WAL pragmas
  models.py          SQLModel schema
  jobs.py            worker thread, progress publishing, cancellation
  routers/jobs.py    job + cue + artifact endpoints
  routers/glossary.py  glossary, terms, CSV round-trip

static/index.html    the UI
tools/               ASR eval + reference-generation harnesses
mt_serve/            MT HTTP service (reworked; never actually run)
```

Stage order: `download → transcode → transcribe → segment → translate →
style → render`.

Job artifacts live in `data/jobs/<id>/`: `job-720p.mp4`, `job.wav`,
`job.json`, `job.cues.json`, `job.ass`, `job.burned.mp4`.

**Four venvs, because torch and llama.cpp need mutually incompatible
builds on Pascal:** torch's last `sm_61` wheel is `2.6.0+cu118`;
llama.cpp must be compiled against CUDA 12.9 with
`CMAKE_CUDA_ARCHITECTURES=61`.

| venv | Python | Contents |
|---|---|---|
| `.venv-app` | 3.14 | FastAPI, SQLModel, yt-dlp — **built** |
| `.venv-asr` | 3.12 | torch 2.6.0+cu118, transformers — **not built** |
| `.venv-mt` | 3.12 | llama-cpp-python built for sm_61 — **not built** |
| `.venv-llm` | 3.14 | suggestion/autocomplete LLM — **not built, no viable model** |

---

## 6. What is built and verified

Each item was executed, not merely written.

**Pipeline package** — seven stages, each importable *and* a CLI
(`python -m pipeline.<stage>`). `paths.py` centralises the artifact layout
that the old flat scripts scattered across the working directory.

**Backend** — FastAPI + SQLModel on SQLite (WAL, foreign keys on);
threaded job runner; WebSocket progress with snapshot-on-connect;
glossary router with UTF-8-BOM CSV export and merge-or-replace import;
artifact serving with a path-traversal guard.

**UI** — `static/index.html`: job list, live stage rail, glossary and term
management with CSV in/out.

**Configuration** — `backend/config.py` resolves model paths from
environment variables first, then `[tool.ncat]` in `pyproject.toml`. No
path is hardcoded anywhere in the tree.

**Verified by execution:**
- schema creation, term CRUD, glossary CRUD
- CSV export (BOM present) and import merge (`added/updated/skipped`)
- `/api/models` correctly reports both ASR models present
- `segment` → `style` producing ASS with the unanchored line flagged
  `needs_timing` and emitted at the `0:00:00.00` sentinel
- ASS output is pure CRLF with BOM — Aegisub-ready
- WebSocket delivers a snapshot on connect plus live stage transitions and
  the terminal error event
- job directory traversal rejected
- config resolution from env vars, and unset paths reported as `""` rather
  than guessed
- every endpoint named in the README's API table answers, and every
  `python -m pipeline.<stage> --help` plus `main.py --list-stages` runs
- `legacy/` scripts still resolve their intra-directory imports after the
  move, and the active tree imports none of them

**Tooling:** a local ranged-parallel downloader overcame per-connection
throttling (16 KB/s single-stream → 3.3 MB/s across 8 connections) and
fetched `cuda-tools-12.9.1-2` with its size verified. It is machine-local
and not part of the repository.

---

## 7. Defects found and fixed

1. **Mixed line endings in exported ASS.** Only the line *joins* were CRLF;
   the header's own newlines were bare LF. Aegisub rewrites line endings on
   save, so an imported file would have differed from its re-exported self.
   Normalised the header; now pure CRLF.
2. **`HTTP_PROXY` hijacking localhost.** The environment sets
   `HTTP_PROXY`, so httpx routed calls to the local MT service through the
   proxy, which answered 502. Fixed with `trust_env=False`. This would have
   broken translation in production, not just in tests.
3. **`DetachedInstanceError` in the job thread and the WebSocket handler.**
   Attributes were read after the SQLAlchemy session closed. Values are now
   copied inside the session block.
4. **Jobs created without a URL never started.** The auto-start condition
   required a URL or upload path, so a job running from already-present
   media silently stayed `pending`. Now gated on `auto_start`.
5. **WebSocket showed an empty rail on reload.** No state was replayed on
   connect, so a browser attaching to a finished job saw nothing. Added a
   server-side snapshot.

---

## 8. What is not done

**Blocked on a sudo run:**
- `ffmpeg` is not installed. `download`, `transcode` and `render` need it.
- `cuda-tools-12.9.1-2` is downloaded and size-verified, staged outside the
  repository at `/tmp/kilo/pkgs/` (temporary; re-fetch if `/tmp` is cleared)
- Port 80 is not bound. The service runs on `127.0.0.1:8099` for testing;
  `--serve` defaults to `0.0.0.0:80` and needs
  `sudo setcap cap_net_bind_service=+ep .venv-app/bin/python`.

**Blocked on model venvs:**
- `.venv-asr` — `torch-2.6.0+cu118` was still downloading at the time of
  writing. No stage has run against a real model.
- `.venv-mt` — llama-cpp-python must be compiled for `sm_61`.
- `.venv-llm` — no viable model on this hardware.

**Not started:** the bilingual cue editor (P1 below, the core gap);
waveform and timeline; translation memory population; punch-list classifier;
Aegisub round-trip regression suite; any real video through all seven stages.

### Known-broken, do not rely on

- **`mt_serve/` has been reworked but never run.** Its registry no longer
  carries hardcoded paths, but the `llama-cpp-python` build it depends on is
  not installed yet, so the translate stage has never had a working backend.
- **`ASR.md` was deleted** (Parakeet-specific). `tools/eval_line_anchored.py`
  is the surviving harness.

---

## 9. Inconsistencies in the tree

1. **`pyproject.toml` is self-contradictory** — `[tool.setuptools] packages`
   alongside `[tool.uv] package = false`.
2. **`data/ncat.db` holds smoke-test rows** — a "Bench Notes" glossary with
   four imported terms, and several throwaway jobs.
3. **`README_zh-cn.md` removed.** It was the only document still describing
   the retired Windows/Parakeet architecture and would have misled readers.
4. **`status.md` (this file) records one machine's state.** It is useful
   in-repo but will drift for anyone else. The provisioning scripts
   (`setup.sh`, `pget.sh`) are excluded from git for the same reason — they
   carry a hardcoded share address and mountpoint.
5. **Nothing is committed.** All of this work sits uncommitted.

---

## 10. Plan

Ordered by what unblocks the most. P0 is hygiene; P1 is the core gap.

### P0 — Cleanup and single source of truth
- [x] Rework `mt_serve/config.py` — hardcoded paths removed, registry resolves
      from env vars or a local `.gguf`
- [x] Make configuration portable — `backend/config.py`, no baked-in paths
- [x] Trim `.gitignore` to genuinely-needed rules; machine provisioning
      scripts (`setup.sh`, `pget.sh`) excluded as local-only
- [x] Rewrite the README's Hardware and Configuration sections to be
      path-agnostic
- [x] Move the superseded scripts to `legacy/` with a mapping README, per
      the decision to keep them for reference
- [ ] Fix `pyproject.toml`: `[tool.setuptools] packages` vs `[tool.uv] package = false`
- [ ] Clear smoke-test rows from `data/ncat.db`
- [ ] First git commit

### P1 — Bilingual cue editor  ← the core missing piece
- [ ] Cue list view: EN + 中文 stacked, ordered by index
- [ ] Inline edit via `PATCH /api/jobs/{id}/cues/{cid}`
- [ ] Validators in the UI: cps per language, line length, minimum duration
- [ ] Highlight `needs_timing` cues — the ones the segmenter could not anchor
- [ ] `.ass` import into the editor, and export back
- [ ] Keyboard: Tab next, Ctrl+Enter commit, J/K jump

### P2 — Get the unbuilt stages running
- [ ] Install `cuda-tools-12.9.1-2`; pin `IgnorePkg = cuda cuda-tools cudnn` inside `[options]` so a later `-Syu` cannot replace it with CUDA 13 (drops sm_50–sm_70)
- [ ] Build `.venv-asr`, verify a real WAV through Qwen3-ASR + ForcedAligner
- [ ] Re-calibrate the timing offset: the `+341/+359 ms` figures were fitted to Parakeet, and Qwen3-ASR has a different bias (~−370 ms). Currently a single undocumented `DEFAULT_OFFSET_MS = 300`.
- [ ] Compile llama-cpp-python for `sm_61`, build `.venv-mt`
- [ ] **Compare the two MT candidates on translation quality** — build a
      fixed prompt set covering register, punctuation, brand names, numbers
      and long sentences; score both; delete the loser and drop the
      `--mt-model` requirement. Throughput numbers alone are not evidence.
- [ ] Bind port 80 via `setcap`
- [ ] Run one real video through all seven stages

### P3 — Audio and timeline
- [ ] Waveform (numpy peaks → JSON, drawn client-side)
- [ ] Word-level markers from `job.json`
- [ ] Playhead ↔ selected cue sync, region loop
- [ ] Retiming by dragging

### P4 — Glossary and memory, completed
- [ ] Translation memory: populate on approved cues, suggest on exact match while typing
- [ ] Glossary picker inline in the cue editor, with cite counts
- [ ] Style rules (e.g. "keep brand names in English")
- [ ] Glossary context fed into the MT prompt, not only pre-substitution

### P5 — Punch list
- [ ] Classifier: timing violations, terminology conflicts, low-confidence audio, missing translations
- [ ] Punch-item view with Accept / Edit / Keep keyboard flow
- [ ] Agent suggestions recorded with reason, individually undoable

### P6 — Assistant
- [ ] Blocked on hardware. Pascal `sm_61` cannot run FP8, and the available Qwen3 models are ~54 GB BF16. Needs either a smaller model or different hardware.

---

## 11. Risks

- **Aegisub round-trip** — exotic override tags (`\p` drawings, `\t`
  animations) are untested. Only CRLF/BOM/structure are verified. Import →
  export → diff needs a regression suite before trusting the editor.
- **Alignment regression** — the greedy-LCS + LIS algorithm was rewritten
  from `legacy/Full-auto_M134A16.py`. Verified on synthetic input (11 of 17 tokens
  anchored, unanchored line correctly flagged) but **not** on a real
  Nixiesub video. Treat as unproven until P2 runs one.
- **Single writer, no auth** — the service is on the LAN with no
  authentication and a file-download endpoint. Fine for a trusted network,
  unacceptable otherwise.
- **Port 80 without TLS** — cleartext on the LAN; credentials and glossaries
  travel unencrypted.

---

## 12. Superseded, for reference

Conclusions from earlier sessions that no longer hold:

- **Parakeet as primary ASR.** Removed, along with `ASR.md`,
  `asr_parakeet.py` and the `.venv-asr` NeMo install. The
  `rel_pos_local_attn [128,128]` workaround is moot — it existed only to
  fit long audio through NeMo's conformer.
- **`+341/+359 ms` offset.** Fitted to Parakeet's bias. See P2.
- **Windows as host.** Every hardcoded Windows path is gone from the tree,
  including in `mt_serve/config.py`.
- **`.venv-vllm` holding vLLM.** vLLM cannot run on `sm_61`.
- **Qwen3.8-27B-FP8 in the workspace.** A 146 MB partial repo, mostly
  `.git/lfs`; deleted. Real weights are being fetched elsewhere.
- **EX I CAT editor "dropped"** on 2026-10-16 for lack of UI design. It is
  now the central goal (P1).
- **Gap 1, no orchestrator** — closed: `pipeline/runner.py` plus
  `backend/jobs.py`.
- **Gap 3, `legacy/Full-auto_M134A16.py` untested** — partly closed: its logic was
  extracted and exercised on synthetic input only.

Carried forward from earlier sessions as still-true: the Nixiesubs ASS
format and its Aegisub round-trip requirement; the greedy-LCS + LIS approach
and why it beats textbook backtracking on stuttered ASR output; the
"needs a human" timing sentinel (now `needs_timing`); that llama.cpp
compiled for `sm_61` against CUDA 12.9 is proven on this hardware; and that
Index-Translate-9B Q8 translates correctly at ~29 tok/s.

---

## 13. Environment facts worth keeping

Network observations from this machine. They may not hold elsewhere; treat
them as the reason for the workarounds, not as universal truths.

- `github.com` times out from this box; `codeload.github.com` works.
- `raw.githubusercontent.com` is reachable directly.
- `archive.archlinux.org` throttles per connection but not in aggregate —
  ranged parallel fetches recovered ~200x throughput (16 KB/s → 3.3 MB/s).
- A local proxy is *slower* than a direct connection for archive downloads,
  though it remains useful for GitHub.
- `IgnorePkg` only takes effect inside `[options]` in `pacman.conf`.
- The two removed summaries were never committed to git, so they are
  unrecoverable; their content is absorbed in this document.

---

## 14. Cross-references

- Usage, configuration, API: `README.md`
- Upstream tools: Kilo19, davidzz, Nixiesubs

