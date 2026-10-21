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

import argparse
import json
import os
import re
import tempfile
import wave
from pathlib import Path

from .paths import Report, noop_report

# Default model locations on the Vault share; overridden by pyproject
# [tool.ncat] or --asr-model on the CLI.
DEFAULT_ASR_MODEL = 'Qwen/Qwen3-ASR-1.7B-hf'
DEFAULT_ALIGNER_MODEL = 'Qwen/Qwen3-ForcedAligner-0.6B-hf'

# Qwen3-ASR carries a ~370 ms systematic lead against human-timed ASS.
# Calibrated on the Coffee Gifts reference; see tools/eval_line_anchored.py.
DEFAULT_OFFSET_MS = 300


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


def normalise_word(word: str) -> str:
    """Lowercase, strip punctuation and hyphens, spaces only."""
    return re.sub(r'[^a-z0-9 ]+', '', word.lower().replace('-', ' '))


def _to_ms(value: object, default: int = 0) -> int:
    """Coerce a timestamp field to integer milliseconds.

    Forced-aligner output mixes seconds (float) and pre-scaled integer
    milliseconds depending on the model revision, so normalise here
    instead of trusting either.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        # Values above 1000 are already ms (1000 ms = 1 s is the boundary).
        return int(round(value)) if value >= 1000 else int(round(value * 1000))
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def to_vbjson(words: list[dict], full_text: str) -> dict:
    """Convert aligned words to the VoiceBase JSON the segmenter expects.

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
        if not text:
            # Punctuation-only token: extend the previous word instead of
            # emitting an empty one.
            if out:
                out[-1]['e'] = max(out[-1]['e'], _to_ms(item.get('end_time',
                                                item.get('end_offset'))))
                prev_end = out[-1]['e']
            continue
        start = _to_ms(item.get('start_time', item.get('start_offset')))
        end = _to_ms(item.get('end_time', item.get('end_offset')))
        start = max(start, prev_end)
        end = max(end, start)
        out.append({'p': position, 's': start, 'e': end, 'w': text})
        prev_end = end
        position += 1
    return {'transcript': {'words': out, 'text': full_text}}


def wav_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), 'rb') as w:
        return w.nframes / w.getframerate()


def split_wav(path: str | Path, chunk_seconds: int,
              workdir: str | Path | None = None) -> list[tuple[Path, float]]:
    """Split a PCM WAV into equal chunks. Returns [(path, offset_seconds)]."""
    with wave.open(str(path), 'rb') as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        total = w.nframes
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
        'start_time': _to_ms(ts.get('start_time', ts.get('start_offset', 0))),
        'end_time': _to_ms(ts.get('end_time', ts.get('end_offset', 0))),
    } for ts in timestamps]
    for w in words:
        w['start_time'] += offset_ms
        w['end_time'] += offset_ms
    return words, transcript


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
    # Pascal sm_61 has no bf16 support; fp16 is the correct dtype there.
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    if not use_cuda:
        dtype = torch.float32

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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m pipeline.transcribe',
        description='NCAT stage 3/7: Qwen3-ASR + ForcedAligner.',
    )
    p.add_argument('wav', help='16 kHz mono PCM WAV produced by the transcode stage')
    p.add_argument('--asr-model', default=DEFAULT_ASR_MODEL)
    p.add_argument('--aligner-model', default=DEFAULT_ALIGNER_MODEL)
    p.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
    p.add_argument('--language', default='English',
                   help='Force source language (default: English)')
    p.add_argument('--max-new-tokens', type=int, default=4096)
    p.add_argument('--chunk-seconds', type=int, default=0,
                   help='Split longer audio into chunks of this size')
    p.add_argument('--offset-ms', type=int, default=DEFAULT_OFFSET_MS,
                   help='Global timing offset applied to word start/end '
                        f'(default {DEFAULT_OFFSET_MS}; 0 disables)')
    p.add_argument('--out-dir', default=None)
    p.add_argument('--stem', default=None)
    args = p.parse_args(argv)

    wav = Path(args.wav).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else wav.parent
    stem = args.stem or wav.stem

    try:
        vb, text = transcribe(
            wav,
            asr_model=args.asr_model,
            aligner_model=args.aligner_model,
            device=args.device,
            language=args.language,
            max_new_tokens=args.max_new_tokens,
            chunk_seconds=args.chunk_seconds,
            offset_ms=args.offset_ms,
            report=lambda m: print(m, flush=True),
        )
    except (AsrError, Exception) as exc:
        print(f'transcription failed: {exc}')
        return 1

    (out_dir / f'{stem}.txt').write_text(text, encoding='utf-8')
    (out_dir / f'{stem}.json').write_text(
        json.dumps(vb, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'wrote {stem}.txt ({len(text)} chars) and {stem}.json '
          f'({len(vb["transcript"]["words"])} words)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
