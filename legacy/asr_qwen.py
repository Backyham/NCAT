'''
Qwen3-ASR + Qwen3-ForcedAligner backend for NCAT transcribe pipeline.

STATUS: DISABLED (Oct 2026).

We are currently shipping with Parakeet-TDT-0.6B-v3 as the
primary ASR. This file is kept so that Qwen3-ASR can be
re-enabled later without recreating the integration. To
re-enable, see the activation notes in transcribe.py.

Two-stage pipeline (when enabled):
  1. Qwen3-ASR (transformers AutoModelForMultimodalLM) -> transcript text
  2. Qwen3-ForcedAligner (AutoModelForTokenClassification) -> word-level timestamps

Requires transformers>=5.13.0 and local model dirs:
  - Qwen3-ASR-1.7B-hf (or 0.6B-hf)
  - Qwen3-ForcedAligner-0.6B-hf

Empirical notes (from prior evaluation, see ASR.md):
  - WER ~6.6% (jiwer edit-distance) on the Coffee Gifts video
  - Raw MAE ~380ms vs ASS line GT (with ~-370ms systematic early bias)
  - Apply +300ms global offset to drop MAE to ~190ms
  - Slowest of the three tested backends (RTF ~0.14)
'''
import json
import os
import sys
import time

from exception_proc import show_exception_and_exit

sys.excepthook = show_exception_and_exit

DEFAULT_ASR_MODEL_ID = 'Qwen/Qwen3-ASR-1.7B-hf'
DEFAULT_ALIGNER_MODEL_ID = 'Qwen/Qwen3-ForcedAligner-0.6B-hf'


def _format_text(w: str) -> str:
    import re
    return re.sub(r'[^a-z0-9 ]+', '', w.lower().replace('-', ' '))


def _to_vbjson(timestamps: list[dict], full_text: str) -> dict:
    def _ms(ts: dict, *keys: str) -> int:
        for k in keys:
            if k in ts and ts[k] is not None:
                v = ts[k]
                if isinstance(v, (int, float)):
                    if v < 1000:
                        return int(round(v * 1000))
                    if k in ('start_offset', 'end_offset', 'start', 'end') and v < 100000:
                        return int(round(v * 1000))
                    return int(round(v))
                try:
                    return int(v)
                except (ValueError, TypeError):
                    continue
        return 0

    vb = {'transcript': {'words': []}}
    position = 0
    prev_end = 0
    for ts in timestamps:
        word = ts.get('text', ts.get('word', ts.get('w', ''))).strip()
        start_ms = _ms(ts, 'start_time', 'start_offset', 'start', 's')
        end_ms = _ms(ts, 'end_time', 'end_offset', 'end', 'e')
        start_ms += ts.get('offset_ms', 0)
        end_ms += ts.get('offset_ms', 0)
        vb_text = _format_text(word).replace(' ', '')
        if not vb_text:
            continue
        if start_ms < prev_end:
            start_ms = prev_end
        if end_ms < start_ms:
            end_ms = start_ms
        vb['transcript']['words'].append({
            'p': position,
            's': start_ms,
            'e': end_ms,
            'w': vb_text,
        })
        prev_end = end_ms
        position += 1
    return vb


def _chunk_wave(audio_path: str, chunk_seconds: int) -> list[tuple[str, float]]:
    import wave
    import tempfile
    with wave.open(audio_path, 'rb') as w:
        rate = w.getframerate()
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        nframes = w.getnframes()
        raw = w.readframes(nframes)
    frames_per_chunk = chunk_seconds * rate
    chunks: list[tuple[str, float]] = []
    pos = 0
    offset_s = 0.0
    while pos < nframes:
        take = min(frames_per_chunk, nframes - pos)
        out_path = tempfile.NamedTemporaryFile(
            prefix='qwen_chunk_', suffix='.wav', delete=False,
        ).name
        with wave.open(out_path, 'wb') as cw:
            cw.setnchannels(nchannels)
            cw.setsampwidth(sampwidth)
            cw.setframerate(rate)
            cw.writeframes(raw[pos * (sampwidth * nchannels):
                               (pos + take) * (sampwidth * nchannels)])
        chunks.append((out_path, offset_s))
        pos += take
        offset_s += take / rate
    return chunks


def _run_qwen_chunk(asr_model, asr_processor, aligner_model, aligner_processor,
                    chunk_path: str, offset_s: float,
                    language: str | None,
                    max_new_tokens: int) -> tuple[list[dict], str]:
    import torch
    offset_ms = int(offset_s * 1000)
    req_kwargs = {'audio': chunk_path}
    if language:
        req_kwargs['language'] = language
    inputs = asr_processor.apply_transcription_request(**req_kwargs)
    inputs = inputs.to(asr_model.device, asr_model.dtype)
    with torch.inference_mode():
        output_ids = asr_model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False,
        )
    generated_ids = output_ids[:, inputs['input_ids'].shape[1]:]
    parsed = asr_processor.decode(
        generated_ids, return_format='parsed',
    )[0]
    transcript = parsed.get('transcription') or ''
    detected_lang = parsed.get('language') or language or 'English'
    if not transcript.strip():
        return [], ''

    aligner_inputs, word_lists = aligner_processor.prepare_forced_aligner_inputs(
        audio=chunk_path, transcript=transcript, language=detected_lang,
    )
    aligner_inputs = aligner_inputs.to(aligner_model.device, aligner_model.dtype)
    with torch.inference_mode():
        outputs = aligner_model(**aligner_inputs)
    timestamps = aligner_processor.decode_forced_alignment(
        logits=outputs.logits,
        input_ids=aligner_inputs['input_ids'],
        word_lists=word_lists,
        timestamp_token_id=aligner_model.config.timestamp_token_id,
    )[0]
    out_words = []
    for ts in timestamps:
        st = ts.get('start_time', ts.get('start_offset', 0))
        et = ts.get('end_time', ts.get('end_offset', 0))
        if st > 1000:
            st = st / 1000.0
        if et > 1000:
            et = et / 1000.0
        out_words.append({
            'text': ts.get('text', ts.get('word', '')).strip(),
            'start_time': float(st),
            'end_time': float(et),
            'offset_ms': offset_ms,
        })
    return out_words, transcript


def qwen3_asr(audio: str,
              asr_model_id: str = DEFAULT_ASR_MODEL_ID,
              aligner_model_id: str = DEFAULT_ALIGNER_MODEL_ID,
              device: str = 'auto',
              language: str | None = None,
              max_new_tokens: int = 4096,
              chunk_seconds: int = 0) -> tuple[dict, str]:
    try:
        import torch
        from transformers import (
            AutoProcessor,
            AutoModelForMultimodalLM,
            AutoModelForTokenClassification,
        )
    except ImportError:
        raise SystemError(
            'transformers/torch not installed. '
            'Run: pip install transformers accelerate torch'
        )

    print(f"\nQwen3-ASR backend\n  ASR model     : {asr_model_id}\n  Aligner model : {aligner_model_id}\n")

    t0 = time.time()
    asr_processor = AutoProcessor.from_pretrained(asr_model_id)
    asr_model = AutoModelForMultimodalLM.from_pretrained(
        asr_model_id, device_map=device, dtype=torch.bfloat16,
    ).eval()

    aligner_processor = AutoProcessor.from_pretrained(aligner_model_id)
    aligner_model = AutoModelForTokenClassification.from_pretrained(
        aligner_model_id, device_map=device, dtype=torch.bfloat16,
    ).eval()
    print(f"Models loaded in {time.time() - t0:.1f}s.\n")

    if not os.path.isfile(audio):
        raise SystemError(f'Audio file not found: {audio}')

    chunks: list[tuple[str, float]] = []
    cleanup: list[str] = []
    if chunk_seconds > 0:
        import wave
        with wave.open(audio, 'rb') as w:
            duration_s = w.getnframes() / w.getframerate()
        if duration_s > chunk_seconds:
            print(f'Audio {duration_s:.0f}s exceeds chunk size '
                  f'{chunk_seconds}s; splitting.\n')
            chunks = _chunk_wave(audio, chunk_seconds)
            cleanup = [c[0] for c in chunks]
        else:
            chunks = [(audio, 0.0)]
    else:
        chunks = [(audio, 0.0)]

    all_words: list[dict] = []
    all_text_parts: list[str] = []
    for i, (chunk_path, offset_s) in enumerate(chunks, 1):
        print(f'Chunk {i}/{len(chunks)}: {chunk_path}')
        torch.cuda.empty_cache()
        try:
            chunk_words, chunk_text = _run_qwen_chunk(
                asr_model, asr_processor,
                aligner_model, aligner_processor,
                chunk_path, offset_s, language, max_new_tokens,
            )
        except Exception as e:
            raise SystemError(f'Chunk {i} failed: {e}')
        all_words.extend(chunk_words)
        all_text_parts.append(chunk_text)
    full_text = ' '.join(all_text_parts).strip()

    for p in cleanup:
        try:
            os.remove(p)
        except OSError:
            pass

    t5 = time.time()
    print(f"Total Qwen3-ASR+Aligner time: {t5 - t0:.1f}s "
          f"({len(all_words)} words).\n")
    return _to_vbjson(all_words, full_text), full_text


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Run Qwen3-ASR + ForcedAligner.')
    p.add_argument('audio', help='Path to 16kHz mono PCM WAV')
    p.add_argument('--asr-model', default=DEFAULT_ASR_MODEL_ID,
                   help='HF model id or local directory containing '
                        'model.safetensors + config files.')
    p.add_argument('--aligner-model', default=DEFAULT_ALIGNER_MODEL_ID,
                   help='HF model id or local directory for the forced aligner.')
    p.add_argument('--language', default=None,
                   help='Force language (e.g. English). Default: auto-detect.')
    p.add_argument('--max-new-tokens', type=int, default=4096)
    p.add_argument('--chunk-seconds', type=int, default=0,
                   help='If >0, split audio into chunks of this many '
                        'seconds and transcribe each (avoids OOM on '
                        'long files).')
    args = p.parse_args()

    try:
        vb, text = qwen3_asr(
            args.audio,
            asr_model_id=args.asr_model,
            aligner_model_id=args.aligner_model,
            language=args.language,
            max_new_tokens=args.max_new_tokens,
            chunk_seconds=args.chunk_seconds,
        )
        out = '.'.join(args.audio.split('.')[:-1]) or args.audio
        with open(out + '.txt', 'w', encoding='utf-8') as f:
            f.write(text)
        with open(out + '.json', 'w', encoding='utf-8') as f:
            json.dump(vb, f, ensure_ascii=False, indent=2)
        print(f"Wrote {out}.txt ({len(text)} chars) and "
              f"{out}.json ({len(vb['transcript']['words'])} words)")
    except Exception as e:
        show_exception_and_exit(type(e), e, e.__traceback__)