"""ASR stage: Qwen3-ASR + Qwen3-ForcedAligner -> word-timed transcript.

Replaces the Parakeet backend (Pascal sm_61 cannot run NeMo's kernels) and
absorbs the old ``asr_qwen.py`` + ``transcribe.py`` pair.

Two model passes per chunk:
  1. Qwen3-ASR turns audio into text.
  2. Qwen3-ForcedAligner aligns that text to the audio, giving word
     boundaries.

Output is the VoiceBase-shaped JSON the segment stage already expects:
``{"transcript": {"words": [{"p", "s", "e", "w"}, ...]}}`` where s/e are
integer milliseconds.

This module imports torch/transformers lazily so that ``import pipeline``
stays cheap and the app venv never needs the ASR stack installed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from .config import model_path
from .paths import Report, noop_report

# Resolved at import from NCAT_ASR_MODEL / NCAT_ALIGNER_MODEL, then
# [tool.ncat], then these Hub ids. Point the environment variables at local
# directories to run fully offline.
DEFAULT_ASR_MODEL = model_path('asr_model')
DEFAULT_ALIGNER_MODEL = model_path('aligner_model')

# The +341/+359 ms figures that used to live here were fitted to Parakeet and
# do not transfer to Qwen3-ASR. No offset has been calibrated against a
# Qwen3-timed reference, so the honest default is 0. Re-fit with
# tools/eval_asr.py --sweep-offsets against a human-timed Ground Truth.
DEFAULT_OFFSET_MS = 0


class AsrError(RuntimeError):
    pass


def _require_stack() -> None:
    missing = []
    for name, hint in (
        ('torch', 'torch'),
        ('transformers', 'transformers>=5.13'),
    ):
        try:
            __import__(name)
        except ImportError:
            missing.append(hint)
    if missing:
        raise AsrError(
            'ASR dependencies missing: ' + ', '.join(missing) +
            '. Install them into the ASR venv '
            '(.venv-asr/bin/pip install ' + ' '.join(missing) + ')'
        )


def asr_python() -> str | None:
    """Interpreter that holds torch, or None when this one already does.

    The ASR tier cannot share the web tier's Python: the last PyTorch wheel
    supporting Pascal is cu118, and that line stops at Python 3.13 while the
    web tier runs 3.14. So when the pipeline is driven from the web venv,
    this stage re-executes itself in ``$NCAT_ASR_PYTHON`` (default
    ``.venv-asr/bin/python``) instead of failing on the import.
    """
    try:
        import torch  # noqa: F401
        return None
    except ImportError:
        pass
    configured = os.environ.get('NCAT_ASR_PYTHON', '').strip()
    if not configured:
        candidate = Path(__file__).resolve().parent.parent / '.venv-asr' / 'bin' / 'python'
        configured = str(candidate) if candidate.is_file() else ''
    if configured and Path(configured).is_file():
        return configured
    raise AsrError(
        'torch is not importable here and no ASR interpreter was found.\n'
        'Set NCAT_ASR_PYTHON to the interpreter that holds torch, e.g.\n'
        f'  export NCAT_ASR_PYTHON={candidate or "<path>/.venv-asr/bin/python"}'
    )


def _delegate_to_asr_venv(wav: str | Path, out_dir: str, stem: str,
                          *, asr_model: str | None,
                          aligner_model: str | None, device: str,
                          chunk_seconds: int, offset_ms: int,
                          report: Report) -> None:
    """Re-run this stage as a subprocess in the ASR interpreter."""
    interpreter = asr_python()
    assert interpreter is not None
    command = [interpreter, '-m', 'pipeline.transcribe', str(wav),
               '--out-dir', out_dir, '--stem', stem,
               '--offset-ms', str(offset_ms)]
    if asr_model:
        command += ['--asr-model', asr_model]
    if aligner_model:
        command += ['--aligner-model', aligner_model]
    if device != 'cuda':
        command += ['--device', device]
    if chunk_seconds:
        command += ['--chunk-seconds', str(chunk_seconds)]

    report(f'delegating to ASR interpreter: {interpreter}')
    env = dict(os.environ)
    # Stop the child trying to delegate again if it also lacks torch.
    env['NCAT_ASR_PYTHON'] = ''
    repo_root = str(Path(__file__).resolve().parent.parent)
    env['PYTHONPATH'] = repo_root + os.pathsep + env.get('PYTHONPATH', '')
    completed = subprocess.run(command, env=env, text=True,
                               capture_output=True)
    if completed.stdout:
        for line in completed.stdout.splitlines():
            report(f'  {line}')
    if completed.returncode != 0:
        raise AsrError(
            'ASR stage failed in the ASR venv:\n'
            + (completed.stderr or '').strip()[-1500:]
        )


def normalise_word(word: str) -> str:
    """Lowercase, strip punctuation and hyphens, spaces only."""
    return re.sub(r'[^a-z0-9 ]+', '', word.lower().replace('-', ' '))


def _seconds_to_ms(value: object, default: int = 0) -> int:
    """Convert a forced-aligner timestamp (seconds, float) to int ms.

    The aligner emits seconds as floats -- 0.32 means 320 ms. There is
    deliberately no "is this already milliseconds?" heuristic: guessing by
    magnitude silently scales short words by 1000 while leaving long ones
    alone, which corrupts the whole timeline.
    """
    if value is None:
        return default
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return default


def to_vbjson(words: list[dict], full_text: str) -> dict:
    """Assemble the VoiceBase JSON the segmenter expects.

    Input words must already be in integer milliseconds -- the conversion
    from the aligner's seconds happens once, in _transcribe_chunk. Doing it
    again here was a double-conversion bug that inflated timings 1000x.

    Enforces two invariants that downstream timing assumes: starts never
    precede the previous word's end, and ends never precede their own start.
    Violations are clamped rather than dropped, because dropping shifts
    every later cue.
    """
    out: list[dict] = []
    prev_end = 0
    position = 0
    for item in words:
        text = normalise_word(str(item.get('text', '')).strip()).replace(' ', '')
        try:
            end = int(round(float(item.get('end_time', 0))))
        except (TypeError, ValueError):
            end = prev_end
        if not text:
            # Punctuation-only token: extend the previous word instead of
            # emitting an empty one.
            if out:
                out[-1]['e'] = max(out[-1]['e'], end)
                prev_end = out[-1]['e']
            continue
        try:
            start = int(round(float(item.get('start_time', 0))))
        except (TypeError, ValueError):
            start = prev_end
        start = max(start, prev_end)
        end = max(end, start)
        out.append({'p': position, 's': start, 'e': end, 'w': text})
        prev_end = end
        position += 1
    return {'transcript': {'words': out, 'text': full_text}}


def wav_duration_seconds(path: str | Path) -> float:
    # getnframes() rather than the .nframes attribute: the attribute was
    # removed in Python 3.13 and is already absent on some 3.12 builds.
    with wave.open(str(path), 'rb') as w:
        return w.getnframes() / w.getframerate()


def split_wav(path: str | Path, chunk_seconds: int,
              workdir: str | Path | None = None) -> list[tuple[Path, float]]:
    """Split a PCM WAV into equal chunks. Returns [(path, offset_seconds)]."""
    with wave.open(str(path), 'rb') as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        total = w.getnframes()
        raw = w.readframes(total)

    frames_per_chunk = chunk_seconds * rate
    stride = width * channels
    out: list[tuple[Path, float]] = []
    workdir = Path(workdir) if workdir else Path(tempfile.gettempdir())

    pos = 0
    while pos < total:
        take = min(frames_per_chunk, total - pos)
        fd, name = tempfile.mkstemp(prefix='ncat_chunk_', suffix='.wav',
                                    dir=str(workdir))
        os.close(fd)
        chunk_path = Path(name)
        with wave.open(str(chunk_path), 'wb') as cw:
            cw.setnchannels(channels)
            cw.setsampwidth(width)
            cw.setframerate(rate)
            cw.writeframes(raw[pos * stride:(pos + take) * stride])
        out.append((chunk_path, pos / rate))
        pos += take
    return out


def _transcribe_chunk(asr_model, asr_processor,
                      aligner_model, aligner_processor,
                      chunk_path: Path, offset_s: float,
                      language: str | None,
                      max_new_tokens: int,
                      report: Report) -> tuple[list[dict], str]:
    import torch

    request: dict = {'audio': str(chunk_path)}
    if language:
        request['language'] = language
    inputs = asr_processor.apply_transcription_request(**request)
    inputs = inputs.to(asr_model.device, asr_model.dtype)
    with torch.inference_mode():
        generated = asr_model.generate(**inputs, max_new_tokens=max_new_tokens,
                                       do_sample=False)
    new_ids = generated[:, inputs['input_ids'].shape[1]:]
    parsed = asr_processor.decode(new_ids, return_format='parsed')[0]
    transcript = parsed.get('transcription') or ''
    detected = parsed.get('language') or language or 'English'

    if not transcript.strip():
        report('  chunk produced no text; skipping alignment')
        return [], ''

    align_inputs, word_lists = aligner_processor.prepare_forced_aligner_inputs(
        audio=str(chunk_path), transcript=transcript, language=detected,
    )
    align_inputs = align_inputs.to(aligner_model.device, aligner_model.dtype)
    with torch.inference_mode():
        outputs = aligner_model(**align_inputs)
    timestamps = aligner_processor.decode_forced_alignment(
        logits=outputs.logits,
        input_ids=align_inputs['input_ids'],
        word_lists=word_lists,
        timestamp_token_id=aligner_model.config.timestamp_token_id,
    )[0]

    offset_ms = int(round(offset_s * 1000))
    words = [{
        'text': ts.get('text', ts.get('word', '')).strip(),
        'start_time': _seconds_to_ms(ts.get('start_time', ts.get('start_offset', 0))),
        'end_time': _seconds_to_ms(ts.get('end_time', ts.get('end_offset', 0))),
    } for ts in timestamps]
    for w in words:
        w['start_time'] += offset_ms
        w['end_time'] += offset_ms
    return words, transcript


def _pick_dtype(torch, use_cuda: bool):
    """Choose the fastest dtype the GPU can actually execute.

    ``torch.cuda.is_bf16_supported()`` returns True on a Tesla P40 even
    though Pascal has no bf16 tensor cores -- torch emulates it, and the
    emulation runs at roughly half the fp16 rate while moving twice the
    bytes. Trusting that flag costs about 2x on this hardware.

    Native bf16 starts at compute capability 8.0 (Ampere). Below that the
    card has fp16x2 cores, so fp16 is both faster and smaller.
    """
    if not use_cuda:
        return torch.float32
    major, _minor = torch.cuda.get_device_capability(0)
    return torch.bfloat16 if major >= 8 else torch.float16


def transcribe(wav: str | Path,
               asr_model: str = DEFAULT_ASR_MODEL,
               aligner_model: str = DEFAULT_ALIGNER_MODEL,
               device: str = 'cuda',
               language: str | None = 'English',
               max_new_tokens: int = 4096,
               chunk_seconds: int = 0,
               offset_ms: int = DEFAULT_OFFSET_MS,
               report: Report = noop_report) -> tuple[dict, str]:
    """Transcribe a 16 kHz mono WAV. Returns (vbjson, plain_text)."""
    _require_stack()
    import torch
    from transformers import (AutoModelForMultimodalLM,
                              AutoModelForTokenClassification,
                              AutoProcessor)

    wav = Path(wav)
    if not wav.is_file():
        raise AsrError(f'audio file not found: {wav}')

    use_cuda = device.startswith('cuda') and torch.cuda.is_available()
    if device.startswith('cuda') and not use_cuda:
        report('CUDA unavailable; falling back to CPU (this will be slow)')
    target = 'cuda' if use_cuda else 'cpu'
    dtype = _pick_dtype(torch, use_cuda)

    report(f'loading ASR model from {asr_model}')
    asr_processor = AutoProcessor.from_pretrained(asr_model)
    asr_model_obj = (AutoModelForMultimodalLM
                     .from_pretrained(asr_model, device_map=target, dtype=dtype)
                     .eval())
    report(f'loading aligner model from {aligner_model}')
    aligner_processor = AutoProcessor.from_pretrained(aligner_model)
    aligner_model_obj = (AutoModelForTokenClassification
                         .from_pretrained(aligner_model, device_map=target,
                                          dtype=dtype)
                         .eval())

    duration = wav_duration_seconds(wav)
    chunks: list[tuple[Path, float]] = []
    if chunk_seconds > 0 and duration > chunk_seconds:
        report(f'audio is {duration:.0f}s; splitting into {chunk_seconds}s chunks')
        chunks = split_wav(wav, chunk_seconds, workdir=wav.parent)
    else:
        chunks = [(wav, 0.0)]

    all_words: list[dict] = []
    text_parts: list[str] = []
    for index, (chunk_path, offset_s) in enumerate(chunks, 1):
        report(f'chunk {index}/{len(chunks)}')
        if use_cuda:
            torch.cuda.empty_cache()
        try:
            words, text = _transcribe_chunk(
                asr_model_obj, asr_processor,
                aligner_model_obj, aligner_processor,
                chunk_path, offset_s, language, max_new_tokens, report,
            )
        except Exception as exc:
            raise AsrError(f'chunk {index} failed: {exc}') from exc
        all_words.extend(words)
        text_parts.append(text)

    if chunks and chunks[0][0] != wav:
        for chunk_path, _ in chunks:
            chunk_path.unlink(missing_ok=True)

    full_text = ' '.join(t for t in text_parts if t).strip()

    if offset_ms:
        for w in all_words:
            w['start_time'] = max(0, w['start_time'] + offset_ms)
            w['end_time'] = max(0, w['end_time'] + offset_ms)
        report(f'applied +{offset_ms}ms offset to word timings')

    return to_vbjson(all_words, full_text), full_text


def transcribe_stage(job_id: int,
                     report: Report = noop_report,
                     paths=None,
                     wav: str | Path | None = None,
                     asr_model: str | None = None,
                     aligner_model: str | None = None,
                     device: str = 'cuda',
                     chunk_seconds: int = 0,
                     offset_ms: int = DEFAULT_OFFSET_MS,
                     **_ignored) -> tuple[dict, str]:
    """Entry point used by the web runner: writes .txt and .json in place."""
    if paths is None:
        raise ValueError('transcribe stage requires paths')
    if wav is None:
        wav = paths.wav
    if wav is None:
        raise ValueError('no WAV to transcribe')
    paths.require('transcript_txt', 'transcript_json')

    # When torch is not importable here, hand the work to the ASR venv
    # rather than failing: the two tiers cannot share a Python.
    if asr_python() is not None:
        out_dir = str(Path(wav).parent)
        stem = Path(wav).stem
        _delegate_to_asr_venv(wav, out_dir, stem, asr_model=asr_model,
                              aligner_model=aligner_model, device=device,
                              chunk_seconds=chunk_seconds,
                              offset_ms=offset_ms, report=report)
        text = Path(paths.transcript_txt).read_text(encoding='utf-8')
        vb = json.loads(Path(paths.transcript_json).read_text(encoding='utf-8'))
        return vb, text

    _require_stack()

    vb, text = transcribe(
        wav,
        asr_model=asr_model or DEFAULT_ASR_MODEL,
        aligner_model=aligner_model or DEFAULT_ALIGNER_MODEL,
        device=device,
        chunk_seconds=chunk_seconds,
        offset_ms=offset_ms,
        report=report,
    )
    Path(paths.transcript_txt).write_text(text, encoding='utf-8')
    Path(paths.transcript_json).write_text(
        json.dumps(vb, ensure_ascii=False, indent=2), encoding='utf-8')
    report(f'wrote {Path(paths.transcript_txt).name} and '
           f'{Path(paths.transcript_json).name}')
    return vb, text
