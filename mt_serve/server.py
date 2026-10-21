"""FastAPI HTTP server for mt_serve.

Endpoints:
  GET  /health                  - liveness + currently-loaded model
  GET  /models                  - list all registered models + existence
  POST /load/{name}             - hot-swap: load `name`, unload previous
  POST /unload                  - drop the current model
  POST /translate               - single-shot translation
  POST /complete                - auto-complete a partial Chinese line

Run:
  python -m mt_serve.server --model hy-mt-7b --port 8088
"""
from __future__ import annotations

import argparse
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import config
from .backends.base import Backend, build_mt_prompt, human_bytes, probe_vram_bytes
from .backends.llama_cpp_backend import LlamaCppBackend
from .backends.vllm_backend import VLLMBackend


# -- backend factory --------------------------------------------------------

def make_backend(entry: config.ModelEntry) -> Backend:
    if entry.backend == 'llama_cpp':
        return LlamaCppBackend(entry)
    if entry.backend == 'vllm':
        return VLLMBackend(entry)
    raise ValueError(f'Unknown backend: {entry.backend}')


# -- request/response models -----------------------------------------------

class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    source_lang: str = 'en'
    target_lang: str = 'zh'
    max_new_tokens: int = Field(256, ge=1, le=2048)


class TranslateResponse(BaseModel):
    translation: str
    model: str
    tokens: int
    latency_ms: float


class CompleteRequest(BaseModel):
    en_context: str = Field(..., min_length=1, max_length=10000)
    zh_partial: str = ''
    max_new_tokens: int = Field(30, ge=1, le=200)


class CompleteResponse(BaseModel):
    completion: str
    model: str
    tokens: int
    latency_ms: float


class LoadRequest(BaseModel):
    """Body for POST /load/{name} - currently no fields, kept for future."""
    pass


# -- app state -------------------------------------------------------------

class ServerState:
    backend: Optional[Backend] = None
    current_name: Optional[str] = None
    vram_bytes: Optional[int] = None

    def load(self, name: str) -> None:
        if self.backend is not None:
            self.unload()
        entry = config.get(name)
        backend = make_backend(entry)
        backend.load()
        self.backend = backend
        self.current_name = name
        self.vram_bytes = probe_vram_bytes()

    def unload(self) -> None:
        if self.backend is not None:
            self.backend.unload()
        self.backend = None
        self.current_name = None
        # Release GPU memory between loads.
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


state = ServerState()


# -- app -------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load if --model given on CLI.
    if getattr(app, '_cli_model', None):
        try:
            state.load(app._cli_model)
        except Exception as e:
            print(f'[startup] pre-load {app._cli_model} failed: {e}')
    yield
    state.unload()


app = FastAPI(
    title='NCAT MT Server',
    version='0.1.0',
    lifespan=lifespan,
)


@app.get('/health')
def health():
    vram = probe_vram_bytes()
    return {
        'status': 'ok',
        'model_loaded': state.current_name,
        'vram_total': human_bytes(vram),
    }


@app.get('/models')
def list_models():
    out = []
    for e in config.list_all():
        out.append({
            'name': e.name,
            'format': e.format,
            'backend': e.backend,
            'description': e.description,
            'configured': e.exists(),
            'n_ctx': e.n_ctx,
            'path_exists': e.exists(),
            'path': e.resolved_path(),
        })
    return {'models': out}


@app.post('/load/{name}')
def load(name: str):
    try:
        state.load(name)
    except KeyError:
        raise HTTPException(404, f'unknown model {name!r}')
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ImportError as e:
        raise HTTPException(500, str(e))
    except Exception as e:
        raise HTTPException(500, f'load failed: {e}')
    return {'loaded': state.current_name, 'vram_total': human_bytes(state.vram_bytes)}


@app.post('/unload')
def unload():
    state.unload()
    return {'unloaded': True}


@app.post('/translate', response_model=TranslateResponse)
def translate(req: TranslateRequest):
    if state.backend is None:
        raise HTTPException(400, 'no model loaded; POST /load/{name} first')
    t0 = time.time()
    out = state.backend.translate(
        req.text, req.source_lang, req.target_lang, req.max_new_tokens
    )
    # Approximate token count via whitespace split for cross-backend use.
    n_tok = len(out.split())
    return TranslateResponse(
        translation=out,
        model=state.current_name or '',
        tokens=n_tok,
        latency_ms=(time.time() - t0) * 1000,
    )


@app.post('/complete', response_model=CompleteResponse)
def complete(req: CompleteRequest):
    if state.backend is None:
        raise HTTPException(400, 'no model loaded; POST /load/{name} first')
    t0 = time.time()
    out = state.backend.complete(
        req.en_context, req.zh_partial, req.max_new_tokens
    )
    return CompleteResponse(
        completion=out,
        model=state.current_name or '',
        tokens=len(out.split()),
        latency_ms=(time.time() - t0) * 1000,
    )


# -- CLI -------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='NCAT MT server')
    p.add_argument('--host', default='127.0.0.1', help='bind address')
    p.add_argument('--port', type=int, default=8088, help='bind port')
    p.add_argument('--model', default=None,
                   help='pre-load a model from REGISTRY on startup')
    args = p.parse_args()

    app._cli_model = args.model

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
