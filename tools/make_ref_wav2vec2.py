"""
Generate ref.json using wav2vec2 base 960h GREEDY CTC decode.

Uses greedy CTC (not forced_align) to get word-level timestamps.
The greedy decode gives the model's own word sequence with
frame-level timing - completely independent of Qwen/Parakeet/Canary.

Note: predicted text may differ slightly from human-written ASS
text (e.g. "SERF" vs "surf"), but the TIMING is what we need
for MAE evaluation.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import wave
from pathlib import Path

import torch


def chunk_wave_pcm(audio_path: str, chunk_seconds: int):
    with wave.open(audio_path, 'rb') as w:
        rate = w.getframerate()
        nc = w.getnchannels()
        sw = w.getsampwidth()
        nframes = w.getnframes()
        raw = w.readframes(nframes)
    fpc = chunk_seconds * rate
    out = []
    pos = 0
    off = 0.0
    while pos < nframes:
        take = min(fpc, nframes - pos)
        p = tempfile.NamedTemporaryFile(
            prefix='w2v_chunk_', suffix='.wav', delete=False).name
        with wave.open(p, 'wb') as cw:
            cw.setnchannels(nc); cw.setsampwidth(sw); cw.setframerate(rate)
            cw.writeframes(raw[pos * (sw * nc): (pos + take) * (sw * nc)])
        out.append((p, off, rate))
        pos += take
        off += take / rate
    return out


def greedy_ctc_words(model, processor, audio_path: str,
                     frame_ms: float = 20.0) -> list[dict]:
    """Run greedy CTC, group into words using '|' separator.
    Returns list of {w, s_ms, e_ms} in chunk-local time.
    """
    import soundfile as sf
    data, sr = sf.read(audio_path, dtype='float32')
    if data.ndim > 1:
        data = data.mean(axis=1)
    waveform = torch.from_numpy(data)
    if torch.cuda.is_available():
        waveform = waveform.cuda()

    with torch.inference_mode():
        logits = model(waveform.unsqueeze(0)).logits[0]
    pred_ids = torch.argmax(logits, dim=-1).tolist()

    char_positions: list[tuple[str, int]] = []
    prev = None
    for frame_idx, tid in enumerate(pred_ids):
        if tid != 0 and tid != prev:  # blank=0, skip duplicates
            tok = processor.tokenizer.convert_ids_to_tokens([tid])[0]
            if tok not in ('<s>', '</s>', '<pad>', '<unk>'):
                char_positions.append((tok, frame_idx))
        prev = tid

    words: list[dict] = []
    cur_chars: list[tuple[str, int]] = []
    for tok, fr in char_positions:
        if tok == '|':
            if cur_chars:
                text = ''.join(c[0] for c in cur_chars).strip().lower()
                if text:
                    s_ms = int(cur_chars[0][1] * frame_ms)
                    e_ms = int((cur_chars[-1][1] + 1) * frame_ms)
                    if e_ms <= s_ms:
                        e_ms = s_ms + 20
                    words.append({'w': text, 's': s_ms, 'e': e_ms})
                cur_chars = []
        else:
            cur_chars.append((tok, fr))
    if cur_chars:
        text = ''.join(c[0] for c in cur_chars).strip().lower()
        if text:
            s_ms = int(cur_chars[0][1] * frame_ms)
            e_ms = int((cur_chars[-1][1] + 1) * frame_ms)
            if e_ms <= s_ms:
                e_ms = s_ms + 20
            words.append({'w': text, 's': s_ms, 'e': e_ms})
    return words


def main():
    parser = argparse.ArgumentParser(
        description='Generate ref.json via wav2vec2 greedy CTC decode.'
    )
    parser.add_argument('audio', help='16kHz mono PCM WAV path')
    parser.add_argument('--out', default=None)
    parser.add_argument('--source', default='')
    parser.add_argument('--url', default='')
    parser.add_argument('--model', default='facebook/wav2vec2-base-960h')
    parser.add_argument('--chunk-seconds', type=int, default=60)
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.is_file():
        sys.exit(f'Audio not found: {audio_path}')

    os.environ['HF_HUB_OFFLINE'] = '0'
    from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC
    print(f'Loading {args.model}...')
    processor = Wav2Vec2Processor.from_pretrained(args.model)
    model = Wav2Vec2ForCTC.from_pretrained(args.model).eval()
    if torch.cuda.is_available():
        model = model.cuda()
    print(f'On device: {next(model.parameters()).device}')

    with wave.open(str(audio_path), 'rb') as w:
        duration_s = w.getnframes() / w.getframerate()
        duration_ms = int(duration_s * 1000)
    print(f'Audio: {duration_s:.1f}s')

    chunks = chunk_wave_pcm(str(audio_path), args.chunk_seconds)
    print(f'Splitting into {len(chunks)} chunk(s) of '
          f'{args.chunk_seconds}s.')

    all_words: list[dict] = []
    cleanup: list[str] = []
    for i, (cpath, off_s, _) in enumerate(chunks, 1):
        off_ms = int(off_s * 1000)
        print(f'  Chunk {i}/{len(chunks)}: '
              f'offset {off_ms}ms ({off_s:.1f}s)')
        torch.cuda.empty_cache()
        try:
            words = greedy_ctc_words(model, processor, cpath)
        except Exception as e:
            print(f'    Chunk {i} failed: {e}')
            continue
        for w in words:
            w['s'] += off_ms
            w['e'] += off_ms
        all_words.extend(words)
        cleanup.append(cpath)

    for p in cleanup:
        try:
            os.remove(p)
        except OSError:
            pass

    norm = []
    p = 0
    for w in all_words:
        text = re.sub(r"[^a-z0-9 ]+", '', w['w'].lower()).strip()
        if not text:
            continue
        norm.append({'p': p, 's': int(w['s']), 'e': int(w['e']),
                     'w': text})
        p += 1
    ref = {
        'source': args.source,
        'url': args.url,
        'duration_ms': duration_ms,
        'language': 'en',
        'samplerate': 16000,
        'aligner': args.model + ' (greedy CTC)',
        'transcript': {'words': norm},
    }
    out_path = (Path(args.out) if args.out
                else audio_path.with_suffix('.w2v.ref.json'))
    with out_path.open('w', encoding='utf-8') as f:
        json.dump(ref, f, ensure_ascii=False, indent=2)
    print(f'Wrote {len(norm)} words to {out_path}')


if __name__ == '__main__':
    main()