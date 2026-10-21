# NCAT: Nick's Computer-Aided Translation Suite

Browser-based subtitle studio: agents draft, you decide.

Derived from Nixiesubs' workflow. Original tools by Kilo19, matching
algorithm by davidzz. This repository reworks them into a self-hosted web
service.

Current state, known gaps, and the plan: [`status.md`](status.md).

---

## What it does

A video goes in; a reviewed bilingual ASS comes out. The pipeline runs
seven stages, and each one is also runnable on its own:

| # | Stage | Module | What it does |
|---|-------|--------|--------------|
| 1 | download | `pipeline/download.py` | yt-dlp fetch (YouTube, Floatplane, generic) |
| 2 | transcode | `pipeline/transcode.py` | 720p H.264 proxy + 16 kHz mono PCM WAV |
| 3 | transcribe | `pipeline/transcribe.py` | Qwen3-ASR + Qwen3-ForcedAligner → word timings |
| 4 | segment | `pipeline/segment.py` | word timings → bilingual cues (greedy LCS + LIS) |
| 5 | translate | `pipeline/translate.py` | EN → 中文 via the MT service, glossary applied first |
| 6 | style | `pipeline/style.py` | cues → ASS (Nixiesubs look, CRLF + BOM for Aegisub) |
| 7 | render | `pipeline/render.py` | burn the ASS in with FFmpeg |

ASR is **Qwen3-ASR + Qwen3-ForcedAligner**, not Parakeet: the Tesla P40 is
Pascal `sm_61` and cannot run NeMo's kernels.

---

## Hardware

Developed against a **Tesla P40, 24 GB, Pascal `sm_61`**, which constrains
the model choices below. Other CUDA cards should work; see *Constraints* for
the ones that do not.

ASR needs a HuggingFace model directory; MT needs a `.gguf` file:

| Role | Model | Size | Status |
|---|---|---|---|
| ASR | `Qwen3-ASR-1.7B-hf` | ~4 GB | in use |
| Word timings | `Qwen3-ForcedAligner-0.6B-hf` | ~1.2 GB | in use |
| MT candidate A | `Index-Translate-9B.Q8_0.gguf` | ~9.8 GB | under evaluation |
| MT candidate B | `HY-MT2-7B-Q8_0.gguf` | ~8.0 GB | under evaluation |

The two MT models are **candidates competing for one slot**, not a primary
and a fallback. Neither is chosen yet — throughput has been measured, but
translation quality has not, and throughput is not quality. One will be
removed once they have been compared. Point `NCAT_MT_MODEL` at whichever you
have settled on.

`GET /api/models` reports which are actually present, so a missing model
surfaces before a run starts rather than minutes into it.

### Constraints

- **Pascal (`sm_61`) cannot run FP8.** Qwen3 weights shipped as FP8 are
  unusable on such a card; BF16 versions are ~54 GB.
- **NeMo does not support Pascal at all** — the reason ASR is Qwen3-ASR
  rather than Parakeet.
- **vLLM cannot run on `sm_61`.** MT goes through llama.cpp.
- **llama.cpp must be compiled for `sm_61`**; distro builds usually target
  newer architectures only.
- **torch's last `sm_61` wheel is `2.6.0+cu118`**; newer releases emit
  `sm_75+` only.

---

## Install

System packages — `ffmpeg` is required by the download, transcode and render
stages:

```bash
# Arch
sudo pacman -S --needed ffmpeg
# Debian / Ubuntu
sudo apt install ffmpeg
```

Model storage is yours to arrange. Point the environment variables at
whatever holds your model files (see *Configuration*). If you keep them on a
network share, mount it with `_netdev,nofail` so a missing server cannot
block boot.

Create the venvs with `uv`:

```bash
uv venv --python 3.14 .venv-app                 # FastAPI, SQLModel, yt-dlp
uv venv --python 3.12 .venv-asr                 # torch + transformers (ASR)
uv venv --python 3.12 .venv-mt                  # llama-cpp-python (MT)
uv venv --python 3.14 .venv-llm                 # suggestion/autocomplete LLM
```

Four venvs rather than one because torch and llama-cpp-python need
mutually incompatible builds on Pascal: torch's last `sm_61` wheel is
`2.6.0+cu118`, while llama.cpp has to be compiled against CUDA 12.9 with
`CMAKE_CUDA_ARCHITECTURES=61`.

Grant the interpreter permission to bind port 80 without running as root:

```bash
sudo setcap cap_net_bind_service=+ep .venv-app/bin/python
```

---

## Run

```bash
python main.py --serve              # web service on http://<lan-ip>/
python main.py URL                  # download → transcode → transcribe
python main.py --list-stages
python main.py --stages transcode,transcribe video.mp4
python main.py --mt-model index-translate-9b-q8 --stages translate,style job.wav
```

The translate stage requires an explicit `--mt-model`. There is no default
because two MT models are still being evaluated and neither has been chosen.

Individual stages:

```bash
python -m pipeline.download URL
python -m pipeline.transcode video.mp4
python -m pipeline.transcribe audio.wav
python -m pipeline.render video.mp4 subs.ass
```

---

## Configuration

Nothing in this repository hardcodes a path. Model locations come from
environment variables, or from `[tool.ncat]` in `pyproject.toml` as a
fallback:

```bash
export NCAT_ASR_MODEL=/path/to/Qwen3-ASR-1.7B-hf
export NCAT_ALIGNER_MODEL=/path/to/Qwen3-ForcedAligner-0.6B-hf
export NCAT_MT_MODEL=/path/to/Index-Translate-9B.Q8_0.gguf
```

| Variable | Meaning | Default |
|---|---|---|
| `NCAT_ASR_MODEL` | Qwen3-ASR model directory | unset |
| `NCAT_ALIGNER_MODEL` | Forced aligner directory | unset |
| `NCAT_MT_MODEL` | MT `.gguf` | unset |
| `NCAT_LLM_MODEL` | suggestion/autocomplete model | unset |
| `NCAT_MODELS_ROOT` | model root, for convenience | unset |
| `NCAT_DATA_DIR` | SQLite DB + job artifacts | `./data` |
| `NCAT_MT_URL` | MT service base URL | `http://127.0.0.1:8088` |

`mt_serve` resolves its own paths separately: either
`NCAT_MT_<NAME>_PATH` (e.g. `NCAT_MT_INDEX_TRANSLATE_9B_Q8_PATH`) or a
`.gguf` dropped beside `mt_serve/config.py`.

---

## The glossary

The durable asset. A glossary is a named term collection; terms inside it
are applied to the source text *before* the MT model sees it, longest match
first, so `probe` is already `对刀探针` when the model runs. Bulk editing is
CSV in both directions:

```
GET  /api/glossaries/{id}/terms.csv     # UTF-8 BOM, opens cleanly in Excel
POST /api/glossaries/{id}/terms.csv     # merge; ?replace=true to overwrite
```

Columns: `source,target,target_lang,note,confirmed`. Import matches on
`(source, target_lang)`; new pairs are inserted, existing ones updated.

---

## HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | the editor UI |
| GET | `/health` | liveness |
| GET | `/api/models` | which model files are present |
| POST | `/api/jobs` | create and start a job |
| GET | `/api/jobs` | recent jobs |
| GET | `/api/jobs/{id}` | job detail |
| POST | `/api/jobs/{id}/rerun` | re-run, optionally a stage subset |
| POST | `/api/jobs/{id}/cancel` | request cancellation |
| GET | `/api/jobs/{id}/cues` | bilingual cues |
| PATCH | `/api/jobs/{id}/cues/{cid}` | edit a cue |
| GET | `/api/jobs/{id}/files` | artifacts in the job directory |
| WS | `/ws/jobs/{id}` | live progress; sends a snapshot on connect |
| GET/POST | `/api/glossaries` | list / create |
| GET/PATCH/DELETE | `/api/glossaries/{id}` | single glossary |
| GET/POST | `/api/glossaries/{id}/terms` | list / add terms |
| PATCH/DELETE | `/api/glossaries/{id}/terms/{tid}` | single term |
| GET/POST | `/api/glossaries/{id}/terms.csv` | CSV round-trip |

---

## Layout

```
pipeline/          stages; each is both a module and a CLI
  paths.py         stage order + JobPaths (the artifact layout)
  runner.py        stage dispatch shared by the CLI and the web thread
backend/
  app.py           FastAPI app, WebSocket, static hosting
  config.py        model-path resolution (env var, then pyproject, then unset)
  db.py            engine, session, WAL pragmas
  models.py        SQLModel schema
  jobs.py          worker thread + progress publishing
  routers/         jobs, glossary
static/index.html  the UI
tools/             ASR eval + reference-generation harnesses
mt_serve/          MT HTTP service
legacy/            superseded scripts, kept for reference only — see its README
```

Job artifacts live in `data/jobs/<id>/`: `job-720p.mp4`, `job.wav`,
`job.json` (word timings), `job.cues.json`, `job.ass`, `job.burned.mp4`.

---

## Legal

MIT License. Original Nixiesubs tools by Kilo19 and davidzz; thanks also to
dummyx for the early guidance.

Vibe-coded using MiniMax M3.