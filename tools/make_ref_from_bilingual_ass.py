"""
Generate ref.json from a bilingual Nixiesubs-style ASS file.

Extracts human-verified English text (after \\r英 marker), then
aligns it to the source audio using Qwen3-ForcedAligner for
word-level timestamps.

For long audio (>5 min, ForcedAligner limit), the audio is
chunked and results concatenated.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


def extract_english_lines(ass_path: str) -> list[tuple[str, str, str]]:
    """Returns [(start_str, end_str, english_text), ...] from bilingual ASS.
    Skips Comment lines and lines without English text.
    """
    out = []
    with open(ass_path, 'r', encoding='utf-8-sig', errors='replace') as f:
        in_events = False
        for raw in f:
            line = raw.rstrip('\r\n')
            if line.startswith('[Events]'):
                in_events = True
                continue
            if in_events and line.startswith('['):
                in_events = False
            if not in_events:
                continue
            if not line.startswith('Dialogue:'):
                continue
            _, payload = line.split(':', 1)
            parts = payload.split(',', 9)
            if len(parts) < 10:
                continue
            start = parts[1]
            end = parts[2]
            text = parts[9]
            if r'\r英' not in text:
                continue
            en = text.split(r'\r英', 1)[1]
            en = re.sub(r'\{\\[^}]*\}', '', en)
            en = en.replace(r'\N', ' ').replace(r'\n', ' ').replace(r'\h', ' ')
            en = en.strip()
            if en:
                out.append((start, end, en))
    return out


def chunk_wave_pcm(audio_path: str, chunk_seconds: int) -> list[tuple[str, float]]:
    with wave.open(audio_path, 'rb') as w:
        rate = w.getframerate()
        nc = w.getnchannels()
        sw = w.getsampwidth()
        nframes = w.getnframes()
        raw = w.readframes(nframes)
    fpc = chunk_seconds * rate
    out: list[tuple[str, float]] = []
    pos = 0
    off = 0.0
    while pos < nframes:
        take = min(fpc, nframes - pos)
        p = tempfile.NamedTemporaryFile(
            prefix='gt_chunk_', suffix='.wav', delete=False,
        ).name
        with wave.open(p, 'wb') as cw:
            cw.setnchannels(nc); cw.setsampwidth(sw); cw.setframerate(rate)
            cw.writeframes(raw[pos * (sw * nc): (pos + take) * (sw * nc)])
        out.append((p, off))
        pos += take
        off += take / rate
    return out


def find_lines_in_range(en_lines, start_ms: int, end_ms: int) -> str:
    """Concatenate English text whose time range overlaps [start_ms, end_ms]."""
    parts = []
    for (s_str, e_str, en) in en_lines:
        s_ms = ass_ts_to_ms(s_str)
        e_ms = ass_ts_to_ms(e_str)
        if e_ms <= start_ms or s_ms >= end_ms:
            continue
        parts.append(en)
    return ' '.join(parts)


def ass_ts_to_ms(ts: str) -> int:
    h, m, rest = ts.split(':')
    s, cs = rest.split('.')
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000
            + int(cs.ljust(2, '0')[:2]) * 10)


def ms_to_ass_ts(ms: int) -> str:
    h = ms // 3600000
    m = (ms // 60000) % 60
    s = (ms // 1000) % 60
    cs = (ms % 1000) // 10
    return f'{h:d}:{m:02d}:{s:02d}.{cs:02d}'


def normalize_word(w: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", '', w.lower().replace('-', ' ')).strip()


def build_ref_from_chunks(results: list[dict], source: str, url: str,
                          duration_ms: int) -> dict:
    norm = []
    p = 0
    for w in results:
        text = normalize_word(w.get('text', w.get('word', '')))
        if not text:
            continue
        norm.append({
            'p': p,
            's': int(w['s']),
            'e': int(w['e']),
            'w': text,
        })
        p += 1
    return {
        'source': source,
        'url': url,
        'duration_ms': duration_ms,
        'language': 'en',
        'samplerate': 16000,
        'transcript': {'words': norm},
    }


def run_forced_aligner(asr_processor, asr_model, aligner_processor,
                       aligner_model, audio_path: str, text: str,
                       language: str, offset_ms: int,
                       max_new_tokens: int) -> list[dict]:
    import torch
    if not text.strip():
        return []
    req = {'audio': audio_path}
    if language:
        req['language'] = language
    inputs = asr_processor.apply_transcription_request(**req)
    inputs = inputs.to(asr_model.device, asr_model.dtype)
    with torch.inference_mode():
        out_ids = asr_model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False,
        )
    gen = out_ids[:, inputs['input_ids'].shape[1]:]
    parsed = asr_processor.decode(gen, return_format='parsed')[0]
    detected_lang = parsed.get('language') or language or 'English'
    ainputs, word_lists = aligner_processor.prepare_forced_aligner_inputs(
        audio=audio_path, transcript=text, language=detected_lang,
    )
    ainputs = ainputs.to(aligner_model.device, aligner_model.dtype)
    with torch.inference_mode():
        outputs = aligner_model(**ainputs)
    timestamps = aligner_processor.decode_forced_alignment(
        logits=outputs.logits,
        input_ids=ainputs['input_ids'],
        word_lists=word_lists,
        timestamp_token_id=aligner_model.config.timestamp_token_id,
    )[0]
    result = []
    for ts in timestamps:
        st = ts.get('start_time', ts.get('start_offset', 0))
        et = ts.get('end_time', ts.get('end_offset', 0))
        if st > 1000:
            st = st / 1000.0
        if et > 1000:
            et = et / 1000.0
        result.append({
            'text': ts.get('text', ts.get('word', '')).strip(),
            's': int(round(st * 1000)) + offset_ms,
            'e': int(round(et * 1000)) + offset_ms,
        })
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Generate ref.json from bilingual ASS via '
                    'Qwen3-ForcedAligner for word-level timestamps.'
    )
    parser.add_argument('ass', help='Path to bilingual ASS file')
    parser.add_argument('audio', help='Path to 16kHz mono PCM WAV')
    parser.add_argument('--asr-model', required=True,
                        help='Path to Qwen3-ASR model dir')
    parser.add_argument('--aligner-model', required=True,
                        help='Path to Qwen3-ForcedAligner model dir')
    parser.add_argument('--out', default=None,
                        help='Output ref.json path (default: <ass>.ref.json)')
    parser.add_argument('--source', default='', help='Source video title')
    parser.add_argument('--url', default='', help='Source URL')
    parser.add_argument('--chunk-seconds', type=int, default=240,
                        help='Audio chunk size (ForcedAligner limit: 300s)')
    parser.add_argument('--max-new-tokens', type=int, default=2048)
    args = parser.parse_args()

    ass_path = Path(args.ass)
    audio_path = Path(args.audio)
    if not ass_path.is_file():
        sys.exit(f'ASS not found: {ass_path}')
    if not audio_path.is_file():
        sys.exit(f'Audio not found: {audio_path}')

    en_lines = extract_english_lines(str(ass_path))
    if not en_lines:
        sys.exit('No English lines found in ASS.')
    print(f'Extracted {len(en_lines)} English lines from ASS.')

    os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    from transformers import (
        AutoProcessor, AutoModelForMultimodalLM,
        AutoModelForTokenClassification,
    )
    print('Loading Qwen3-ASR + ForcedAligner from local dirs...')
    asr_processor = AutoProcessor.from_pretrained(args.asr_model)
    asr_model = AutoModelForMultimodalLM.from_pretrained(
        args.asr_model, device_map='auto', dtype=torch.bfloat16,
    ).eval()
    aligner_processor = AutoProcessor.from_pretrained(args.aligner_model)
    aligner_model = AutoModelForTokenClassification.from_pretrained(
        args.aligner_model, device_map='auto', dtype=torch.bfloat16,
    ).eval()

    with wave.open(str(audio_path), 'rb') as w:
        duration_s = w.getnframes() / w.getframerate()
        duration_ms = int(duration_s * 1000)
    print(f'Audio duration: {duration_s:.1f}s')

    chunks = chunk_wave_pcm(str(audio_path), args.chunk_seconds)
    print(f'Splitting into {len(chunks)} chunk(s) of '
          f'{args.chunk_seconds}s each.')

    all_results: list[dict] = []
    cleanup: list[str] = []
    for i, (cpath, off_s) in enumerate(chunks, 1):
        off_ms = int(off_s * 1000)
        cstart_ms = off_ms
        cend_ms = off_ms + args.chunk_seconds * 1000
        text = find_lines_in_range(en_lines, cstart_ms, cend_ms)
        print(f'  Chunk {i}/{len(chunks)}: {len(text)} chars of English text')
        if not text.strip():
            continue
        torch.cuda.empty_cache()
        try:
            results = run_forced_aligner(
                asr_processor, asr_model,
                aligner_processor, aligner_model,
                cpath, text, 'English', off_ms, args.max_new_tokens,
            )
        except Exception as e:
            print(f'  Chunk {i} failed: {e}')
            continue
        all_results.extend(results)
        cleanup.append(cpath)

    for p in cleanup:
        try:
            os.remove(p)
        except OSError:
            pass

    ref = build_ref_from_chunks(
        all_results, args.source, args.url, duration_ms,
    )
    out_path = Path(args.out) if args.out else ass_path.with_suffix('.ref.json')
    with out_path.open('w', encoding='utf-8') as f:
        json.dump(ref, f, ensure_ascii=False, indent=2)
    print(f'Wrote {len(ref["transcript"]["words"])} words to {out_path}')


if __name__ == '__main__':
    main()