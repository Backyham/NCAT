"""MT stage: translate cue source text to the target language.

Talks to a local ``llama-server`` over ``/v1/chat/completions`` rather than
importing a model. Two reasons:

* the llama.cpp build for this GPU is separate from the Python environment
  (it targets ``sm_61``), and the model lives in the server process, so
  there is nothing useful for Python to import;
* the server applies the chat template per request. That matters -- see
  ``mt_models.prompt_template`` for why a batched ``llama-cli -p`` prompt
  silently fails.

The server is started for the duration of the run and stopped after, since
the two candidates do not fit on one 24 GB card and running both at once
would push both to CPU.

No model is a default: the caller must name one, because the two
registered candidates are still under evaluation and exactly one will
survive.
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .mt_models import REGISTRY, get as get_model
from .redivide import redivide
from .paths import JobPaths, Report, noop_report

DEFAULT_SERVER_BIN = '.tools/llama-build/bin/llama-server'
DEFAULT_PORT = 8090

# Reasoning traces the qwen35 hybrid emits before answering. Stripped because
# the monologue is not a deliverable and costs hundreds of tokens per line.
_THINK_OPEN = r'\[/?\s*Start\s+thinking\]|<\s*think\s*>'
_THINK_CLOSE = r'\[/?\s*(?:End\s+)?thinking\]|</?think>'


class TranslateError(RuntimeError):
    pass


# ------------------------------------------------------------- plumbing ----

def strip_thinking(text: str) -> str:
    """Remove a reasoning block, keeping whatever follows its close tag.

    Split on open/close rather than one greedy regex: a pattern using an
    optional-slash class for the closing tag never actually matches, which
    made an unterminated trace swallow the translation as well.
    """
    import re
    start = re.search(_THINK_OPEN, text, re.I)
    if start is None:
        return text
    rest = text[start.end():]
    close = re.search(_THINK_CLOSE, rest, re.I)
    return rest[close.end():] if close else ''


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def resolve_server(binary: str | None) -> str:
    """Locate llama-server, preferring the workspace build."""
    candidates = [binary] if binary else []
    candidates += [
        str(Path(DEFAULT_SERVER_BIN)),
        'llama-server',
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path.resolve())
        found = shutil.which(candidate)
        if found:
            return found
    raise TranslateError(
        'llama-server not found. Build it with:\n'
        '  cmake -S .tools/llama.cpp -B .tools/llama-build -G Ninja '
        '-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61 '
        '-DCMAKE_CUDA_COMPILER=/opt/cuda/bin/nvcc '
        '-DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-14\n'
        '  cmake --build .tools/llama-build --target llama-server'
    )


def start_server(binary: str, model: Path, port: int, *,
                 num_ctx: int, gpu_layers: int,
                 extra_args: list[str],
                 report: Report = noop_report) -> subprocess.Popen:
    """Launch the server and block until it answers /health."""
    import httpx

    cmd = [binary, '-m', str(model), '--host', '127.0.0.1',
           '--port', str(port), '-c', str(num_ctx),
           '-ngl', str(gpu_layers), '-fa', 'on', '--no-webui']
    cmd += extra_args
    report(f'starting llama-server on port {port}')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)

    # Loading a 14-17 GB GGUF from disk is not instant; allow five minutes.
    deadline = time.time() + 300
    while time.time() < deadline:
        if proc.poll() is not None:
            output = (proc.stdout.read() if proc.stdout else '')
            tail = '\n'.join(output.splitlines()[-12:])
            raise TranslateError(
                f'llama-server exited (rc={proc.returncode}):\n{tail}')
        try:
            httpx.get(f'http://127.0.0.1:{port}/health', timeout=2.0,
                      trust_env=False)
            report('server listening (model still loading)')
            return proc
        except Exception:
            time.sleep(2.0)
    proc.kill()
    raise TranslateError(f'llama-server not healthy after 300s on port {port}')


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def request_translation(port: int, prompt: str, *, max_tokens: int,
                        temperature: float,
                        ready_timeout: float = 300.0) -> str:
    """POST one prompt, waiting out model loading.

    ``/health`` returns 200 as soon as the HTTP listener is up, which is
    before a 14-17 GB GGUF has finished loading; the completion endpoint
    answers 503 "Loading model" during that window. Readiness is therefore
    taken from the request result rather than from the health probe.
    """
    import httpx

    body = {
        'model': 'local',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'temperature': temperature,
        'stream': False,
    }
    deadline = time.time() + ready_timeout
    with httpx.Client(timeout=300.0,
                      trust_env=False) as client:   # never proxy localhost
        while True:
            try:
                response = client.post(
                    f'http://127.0.0.1:{port}/v1/chat/completions', json=body)
            except httpx.HTTPError as exc:
                # A timed-out or dropped request is a per-segment failure,
                # not a reason to abandon the remaining segments.
                raise TranslateError(f'MT request error: {exc!r}') from exc
            if response.status_code < 400:
                payload = response.json()
                content = payload['choices'][0]['message'].get('content') or ''
                return strip_thinking(content).strip()
            if response.status_code != 503 or time.time() > deadline:
                raise TranslateError(
                    f'MT request failed ({response.status_code}): '
                    f'{response.text[:300]}')
            time.sleep(2.0)


# ---------------------------------------------------------------- helpers --

def load_cues(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('cues', [])
    return list(data)


def tagger_available() -> bool:
    """Whether the NLTK tagger can run, cached after the first try.

    Shared with ``pipeline.review``: both need the models, and a fresh
    machine may have the package without the data.
    """
    from .review import tagger_available as _available
    return _available()


def source_text_of(cue: dict) -> str:
    return (cue.get('asr_text') or cue.get('en') or '')[:120]


def save_cues(path: str | Path, cues: list[dict]) -> None:
    Path(path).write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding='utf-8')


def apply_glossary(text: str, terms: list[dict]) -> str:
    """Substitute confirmed glossary terms before the model sees the text.

    Longest source first so "tool changer" wins over "changer". Word
    boundaries are only asserted around ASCII, since \\b does not behave
    next to CJK.
    """
    import re

    ordered = sorted(terms, key=lambda t: len(t.get('source', '')), reverse=True)
    for term in ordered:
        source = term.get('source')
        target = term.get('target')
        if not source or not target or not term.get('confirmed', True):
            continue
        pattern = re.escape(source.strip())
        left = r'(?<![A-Za-z0-9])' if source[:1].isascii() else ''
        right = r'(?![A-Za-z0-9])' if source[-1:].isascii() else ''
        text = re.sub(f'{left}{pattern}{right}', target.replace('\\', r'\\'),
                      text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------- chunking --

# Tokens per prompt chunk.
#
# The server's --ctx covers the prompt *and* the generation, so the prompt
# budget cannot simply be the whole window: a 4096-token English chunk yields
# roughly 2400 tokens of Chinese, and 4096 + 2400 overruns 8192. Keeping the
# prompt at half the window leaves room for the completion, which is what
# makes whole-text translation safe without shortening the model's work.
#
# Chunking is what makes long videos possible at all. An 87-minute video is
# about 19,000 tokens of English; no single prompt fits, and both candidates
# fail identically with "exceeds the available context size".
CHUNK_TOKENS = 4096

# Conservative characters-per-token for the split estimate. Real English
# averages nearer 4; under-estimating the token count is how a "safe" chunk
# silently becomes an overflow, so the estimate is deliberately pessimistic.
_CHARS_PER_TOKEN = 3.0


def estimate_tokens(text: str, chars_per_token: float = _CHARS_PER_TOKEN) -> int:
    return max(1, int(len(text) / chars_per_token))


def _sentences(text: str) -> list[str]:
    """Split into sentence-ish units, keeping their trailing whitespace.

    NLTK is used when it is available because it handles abbreviations and
    decimals; the punctuation fallback keeps a fresh install working.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if tagger_available():
        try:
            import nltk
            parts = nltk.sent_tokenize(stripped)
            if parts:
                return parts
        except Exception:
            pass
    parts = re.split(r'(?<=[.!?…])\s+', stripped)
    return [p for p in parts if p.strip()]


def _hard_points(text: str) -> list[tuple[int, int]]:
    """Character spans of clause-level break opportunities."""
    return [(m.start(), m.end())
            for m in re.finditer(r'[.!?…]\s+|[,;:]\s+|\s+', text)]


def chunk_text(text: str, *, chunk_tokens: int = CHUNK_TOKENS,
               chars_per_token: float = _CHARS_PER_TOKEN
               ) -> list[dict]:
    """Split continuous text into chunks that each fit the prompt budget.

    Boundaries land on sentences wherever possible, so each chunk is a
    coherent unit and the translator sees prose rather than a fragment cut
    mid-clause. A single sentence longer than the budget is broken at clause
    punctuation, and only if that still fails is it cut at the limit -- which
    is recorded in ``hard_split`` so the caller can report it rather than
    pretend the chunk was clean.
    """
    text = text.strip()
    if not text:
        return []

    limit_chars = int(chunk_tokens * chars_per_token)
    units: list[tuple[str, bool]] = []

    for sentence in _sentences(text):
        if len(sentence) <= limit_chars:
            units.append((sentence, False))
            continue
        # Oversized sentence: fall back to clause breaks.
        buf = ''
        hard = False
        for piece in re.split(r'(?<=[,;:])\s+', sentence):
            if not piece:
                continue
            if buf and len(buf) + 1 + len(piece) > limit_chars:
                units.append((buf, hard))
                buf, hard = piece, False
            else:
                buf = f'{buf} {piece}'.strip() if buf else piece
            while len(buf) > limit_chars:
                units.append((buf[:limit_chars], True))
                buf = buf[limit_chars:]
        if buf.strip():
            units.append((buf.strip(), hard))

    chunks: list[dict] = []
    buf = ''
    hard = False
    for piece, piece_hard in units:
        if buf and estimate_tokens(buf, chars_per_token) + \
                estimate_tokens(piece, chars_per_token) > chunk_tokens:
            chunks.append({'text': buf, 'hard_split': hard,
                           'tokens': estimate_tokens(buf, chars_per_token)})
            buf, hard = '', False
        buf = f'{buf} {piece}'.strip() if buf else piece
        hard = hard or piece_hard
    if buf.strip():
        chunks.append({'text': buf, 'hard_split': hard,
                       'tokens': estimate_tokens(buf, chars_per_token)})
    return chunks


def completion_budget(prompt_tokens: int, num_ctx: int = 8192,
                      margin: int = 256) -> int:
    """Tokens the model may generate for this prompt.

    Derived rather than fixed: a fixed ``max_tokens`` is either wasteful on
    short chunks or fatal on long ones, because the prompt and the completion
    share one window.
    """
    return max(256, min(4096, num_ctx - prompt_tokens - margin))


# ------------------------------------------------------------------ stage --

def chunk_cues(cues: list[str], cues_per_block: int) -> list[list[str]]:
    """Group cues into blocks of at most ``cues_per_block``.

    The point of whole-text translation is context, so blocks are large. The
    limit is the context window, not readability: a 15-minute video fits in
    one block, an 87-minute one needs several. Cutting at a fixed cue count
    keeps the mapping from input block to output block trivial.

    A "cue" here is one line of the reviewed text file -- the unit the human
    has already approved, and the unit ``pipeline.redivide`` cuts the
    translation back onto.
    """
    if cues_per_block <= 0 or len(cues) <= cues_per_block:
        return [cues] if cues else []
    return [cues[i:i + cues_per_block] for i in range(0, len(cues), cues_per_block)]


def translate_text(text: str, *, model: str, binary: str, port: int = 8090,
                   target_lang: str = 'zh', num_ctx: int = 8192,
                   gpu_layers: int = -1, max_tokens: int | None = None,
                   temperature: float = 0.0,
                   chunk_tokens: int = CHUNK_TOKENS,
                   deadline: float | None = None,
                   max_retries: int = 2,
                   report: Report = noop_report) -> tuple[str, list[dict]]:
    """Translate continuous text in sentence-aligned chunks.

    The text is not sent cue by cue. That is what made the translator repair
    somebody else's line breaks instead of translating prose. Instead it goes
    whole, in chunks large enough to carry real context but small enough that
    the prompt *and* the completion fit the context window together.

    Chunk boundaries land on sentences, so each chunk is coherent prose. The
    completion allowance is derived per chunk from what the prompt leaves
    over, because a fixed ``max_tokens`` is wasteful on short chunks and
    fatal on long ones.

    Returns the translation as one string plus per-chunk notes. A chunk that
    fails is retried, and the text that failed is recorded rather than
    silently replaced by the source.
    """
    entry = get_model(model)
    if not entry.exists():
        raise TranslateError(
            f'model file not found for {model!r} (looked at '
            f'{entry.resolved_path() or "nothing"})')

    chunks = chunk_text(text, chunk_tokens=chunk_tokens)
    if not chunks:
        return '', []

    lang_name = {'zh': 'Simplified Chinese', 'en': 'English'}.get(
        target_lang, target_lang)

    while not _port_free(port):
        port += 1
    report(f'server: {binary}')
    proc = start_server(binary, Path(entry.resolved_path()), port,
                        num_ctx=num_ctx, gpu_layers=gpu_layers,
                        extra_args=list(entry.extra_args), report=report)

    total_est = sum(c['tokens'] for c in chunks)
    report(f'{len(text)} chars, ~{total_est} tokens, {len(chunks)} chunk(s) '
           f'of <= {chunk_tokens} tokens')

    out: list[str] = []
    notes: list[dict] = []
    restarts = 0
    try:
        for b, chunk in enumerate(chunks, 1):
            if deadline is not None and time.time() > deadline:
                notes.append({'chunk': b, 'issue': 'time budget exhausted'})
                break

            prompt = (
                f'Translate the following passage of subtitles into '
                f'{lang_name}.\n'
                f'Render it as flowing Chinese prose. Do not add numbering, '
                f'commentary or blank lines, and do not drop anything.\n\n'
                f'{chunk["text"]}'
            )
            allowance = completion_budget(chunk['tokens'], num_ctx)
            if max_tokens is not None:
                allowance = min(allowance, max_tokens)

            got = ''
            attempt = 0
            while True:
                try:
                    got = request_translation(port, prompt,
                                              max_tokens=allowance,
                                              temperature=temperature)
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt > max_retries:
                        notes.append({'chunk': b, 'issue': 'failed',
                                      'detail': str(exc)[:200]})
                        break
                    report(f'  chunk {b} attempt {attempt} failed ({exc}); '
                           f'restarting server')
                    stop_server(proc)
                    while not _port_free(port):
                        port += 1
                    proc = start_server(binary,
                                        Path(entry.resolved_path()), port,
                                        num_ctx=num_ctx,
                                        gpu_layers=gpu_layers,
                                        extra_args=list(entry.extra_args),
                                        report=report)
                    restarts += 1
            if got:
                out.append(got.strip())
            else:
                out.append('')
            if chunk['hard_split']:
                notes.append({'chunk': b, 'issue': 'hard split',
                              'detail': 'no sentence boundary available; '
                                        'chunk was cut at the token limit'})
            report(f'  chunk {b}/{len(chunks)}: ~{chunk["tokens"]} tok in, '
                   f'{len(got)} chars out (allowance {allowance})')
    finally:
        stop_server(proc)
        report('server stopped')

    if restarts:
        notes.append({'restarts': restarts})
    return '\n\n'.join(out), notes


def _fit_cue_count(translated: list[str], want: int) -> list[str]:
    """Force the result to ``want`` cues so the index mapping still holds."""
    if len(translated) > want:
        return translated[:want]
    while len(translated) < want:
        translated.append('')
    return translated


def translate_cues(job_id: int,
                   paths: JobPaths | None = None,
                   report: Report = noop_report,
                   model: str | None = None,
                   target_lang: str = 'zh',
                   terms: list[dict] | None = None,
                   max_tokens: int | None = None,
                   temperature: float = 0.0,
                   num_ctx: int = 8192,
                   gpu_layers: int = -1,
                   port: int = DEFAULT_PORT,
                   server_bin: str | None = None,
                   max_retries: int = 2,
                   video_seconds: float = 0.0,
                   budget_seconds: float = 0.0,
                   chunk_tokens: int = CHUNK_TOKENS,
                   **_ignored) -> dict:
    """Translate the whole approved text, then divide it back onto the cues.

    Not one request per cue. A translator handed a single line cannot see the
    passage around it, so it cannot hold a register, keep a term consistent or
    resolve a pronoun -- and it spends its attention repairing line breaks
    nobody chose. Sending the text undivided, in blocks bounded only by the
    context window, is what the Ground Truth runs were measured with.

    The trade is that the translation comes back continuous, so the cue
    boundaries have to be recovered afterwards by :mod:`pipeline.redivide`.
    That recovery is approximate (see its module docstring); its confidence is
    reported per run rather than hidden, and a run with weak confidence is not
    something to ship without reading first.

    The input is read from ``job.cues.json``, which the review gate writes when
    a human approves the division -- so the text being translated is the one
    that was actually approved, not the raw transcript.
    """
    if not model:
        raise TranslateError(
            'no MT model selected. The registered candidates are still under '
            'evaluation, so neither is a default; pass model explicitly. '
            'Available: ' + ', '.join(sorted(REGISTRY)))
    if model not in REGISTRY:
        raise TranslateError(
            f'unknown model {model!r}. Available: {", ".join(sorted(REGISTRY))}')
    if paths is None:
        raise TranslateError('translate stage requires paths')

    paths.require('cues_json')
    if not Path(paths.cues_json).is_file():
        raise TranslateError(f'cue file missing: {paths.cues_json}')
    cues = load_cues(paths.cues_json)

    with_text = [c for c in cues if (c.get('en') or '').strip()]
    if not with_text:
        report('nothing to translate')
        return {'translated': 0, 'model': model, 'failed': []}

    # Glossary terms go into the prompt rather than replacing the output, so
    # the model still reads natural English and can register around them.
    lines = [apply_glossary(c['en'].strip(), terms) if terms else c['en'].strip()
             for c in with_text]

    report(f'{len(with_text)} cues, {sum(len(x) for x in lines)} characters '
           f'to translate as one passage')

    binary = resolve_server(server_bin)
    deadline = (time.time() + budget_seconds) if budget_seconds else None
    started = time.time()

    translated_text, notes = translate_text(
        ' '.join(lines), model=model, binary=binary, port=port,
        target_lang=target_lang, num_ctx=num_ctx, gpu_layers=gpu_layers,
        max_tokens=max_tokens, temperature=temperature,
        chunk_tokens=chunk_tokens, deadline=deadline,
        max_retries=max_retries, report=report)

    zh_cues, div_notes = redivide(lines, translated_text)

    weak = [n for n in div_notes if n.get('low_confidence')]
    if div_notes:
        report(f'redivision: {len(div_notes)} note(s)')
        for note in div_notes[:5]:
            report(f'  {note}')
    if weak:
        report(f'WARNING: {len(weak)} cues were matched weakly; read the '
               f'translation before trusting it')

    for cue, zh in zip(with_text, zh_cues):
        cue['zh'] = zh or ''
    save_cues(paths.cues_json, cues)

    elapsed = time.time() - started
    factor = elapsed / video_seconds if video_seconds else 0.0
    empty = sum(1 for c in with_text if not (c.get('zh') or '').strip())
    report(f'translated {len(with_text) - empty}/{len(with_text)} cues '
           f'in {elapsed:.0f}s')

    return {
        'translated': len(with_text) - empty,
        'target_lang': target_lang,
        'model': model,
        'failed': [{'issue': n.get('issue'), 'detail': n.get('detail')}
                   for n in notes],
        'redivision': div_notes,
        'weak_cues': len(weak),
        'seconds': round(elapsed, 1),
        'x_realtime': round(factor, 3),
        'within_budget': (not budget_seconds) or elapsed <= budget_seconds,
    }
