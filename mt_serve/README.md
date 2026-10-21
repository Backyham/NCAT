# NCAT MT Server (`mt_serve`)

Local HTTP service for translation models. The pipeline's translate stage
talks to it over HTTP rather than importing it, because the model backend
(llama.cpp) needs a CUDA build specific to the host GPU and belongs in its
own virtualenv.

## API

| Method | Path          | Purpose |
|--------|---------------|---------|
| GET    | `/health`     | Liveness + currently-loaded model |
| GET    | `/models`     | Registered models, resolved path, existence |
| POST   | `/load/{name}`| Hot-swap: load `name`, unload the previous one |
| POST   | `/unload`     | Drop the current model |
| POST   | `/translate`  | Single-shot translation |
| POST   | `/complete`   | Continue a partial target-language line |

```bash
curl http://127.0.0.1:8088/health
curl -X POST http://127.0.0.1:8088/load/index-translate-9b-q8
curl -X POST http://127.0.0.1:8088/translate \
     -H "Content-Type: application/json" \
     -d '{"text":"today we are going to talk about","max_new_tokens":64}'
curl -X POST http://127.0.0.1:8088/complete \
     -H "Content-Type: application/json" \
     -d '{"en_context":"today we are going to talk about","zh_partial":"今天我们来","max_new_tokens":20}'
```

## Run

```bash
python -m mt_serve.server --model index-translate-9b-q8 --port 8088
python -m mt_serve.benchmark --model index-translate-9b-q8 --output bench.json
```

## Configuring model paths

Nothing is hardcoded. Each registry entry resolves in this order:

1. `NCAT_MT_<NAME>_PATH`, with `-` replaced by `_` and uppercased —
   e.g. `NCAT_MT_INDEX_TRANSLATE_9B_Q8_PATH`
2. a file named `<name>.gguf` beside `mt_serve/config.py`
3. the entry's `path` field, empty by default

`GET /models` reports the resolved path and whether it exists, so a typo
shows up as an entry that will not load rather than as a silent fallback.

```bash
export NCAT_MT_INDEX_TRANSLATE_9B_Q8_PATH=/models/Index-Translate-9B.Q8_0.gguf
```

## Registered models

These are **candidates for a single slot, not a primary and a fallback.**
Neither has been chosen. Throughput was measured on a 24 GB Pascal card with
each model fully resident on the GPU, but **translation quality has never
been assessed**, and throughput is not a proxy for it. One entry is expected
to be removed once the two are compared.

| Name | Size | Throughput | Note |
|---|---|---|---|
| `index-translate-9b-q8` | ~9.8 GB | ~29 tok/s | Candidate A |
| `hy-mt2-7b-q8` | ~8.0 GB | ~17–19 tok/s | Candidate B. **Own chat template, not the Qwen3 one** |

The Hy-MT2 template matters: feeding it a Qwen3-formatted prompt produces
garbage rather than an error.

## Adding a model

Add a `ModelEntry` to `REGISTRY` in `config.py`:

```python
'my-model': ModelEntry(
    name='my-model',
    format='gguf',          # or 'safetensors'
    backend='llama_cpp',     # or 'vllm'
    description='...',
    n_ctx=8192,
    n_gpu_layers=-1,         # -1 = all layers on GPU; lower to spill to CPU
),
```

`n_gpu_layers` is the lever when a model does not fit: lower it and the
remainder runs on CPU.

## Backends

### `llama_cpp` — GGUF

Builds llama.cpp from source. **On older GPUs you must compile for your own
architecture**, because distribution builds usually target newer ones:

```bash
CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61" pip install llama-cpp-python
```

- Quantised weights live in `.gguf` files
- No FP16 tensor cores required
- `n_gpu_layers` controls the CPU/GPU split

### `vllm` — HF safetensors

**Cannot run on Pascal (`sm_61`)**, and CUDA 13 dropped `sm_50`–`sm_70`
entirely. Use it only on newer hardware:

```bash
pip install vllm
```

## Performance notes

Throughput was measured on a 24 GB Pascal card with both models fully
resident on the GPU; see the table above.

**Throughput is not translation quality.** No quality benchmark exists for
this service — that is the open decision blocking the choice between the two
candidates, and it is recorded in `status.md`.

## File layout

```
mt_serve/
  config.py                  ModelEntry + REGISTRY + path resolution
  server.py                  FastAPI app
  benchmark.py               Throughput measurement
  backends/
    base.py                  Backend ABC, prompt builder, VRAM probe
    llama_cpp_backend.py     GGUF
    vllm_backend.py          HF safetensors
  requirements*.txt
```
