r'''
Ground truth helper: Aegisub karaoke ASS -> ref.json.

Aegisub supports per-word timing via the \k, \kf, \ko karaoke
tags. Run this script on a hand-timed .ass file to produce the
ref.json consumed by eval_asr.py.

Supported tag forms inside the Text field:
  {\k<dur>word}              - duration in centiseconds
  {\kf<dur>word}             - same, decimal seconds (rare)
  {\\k<dur>word}             - escaped form on disk

Lines marked Comment: are skipped. Lines with karaoke tags are
emitted; lines without are kept as a single text entry spanning
the line's start/end times.
'''
import argparse
import json
import re
import sys
from pathlib import Path

KARAOKE_TAG_RE = re.compile(r'\{\\?k[fo]?(-?\d+)\}')


def strip_ass_overrides(text: str) -> str:
    text = re.sub(r'\{\\[^}]*\}', '', text)
    return text.replace('\\N', ' ').replace('\\n', ' ').replace('\\h', ' ')


def split_karaoke(text: str, line_start_ms: int) -> tuple[list[dict], int]:
    words = []
    cursor_ms = line_start_ms
    for m in KARAOKE_TAG_RE.finditer(text):
        before = text[:m.start()]
        leftover = strip_ass_overrides(before).strip()
        if leftover:
            words.append({'w': leftover, 's': cursor_ms, 'e': cursor_ms})
        dur_cs = int(m.group(1))
        cursor_ms += dur_cs * 10
        word_text = re.sub(r'\{\\[^}]*\}', '', text[m.end():]).strip()
        word_text = word_text.split('\\N')[0].split('\\n')[0].strip()
        if word_text:
            words.append({'w': word_text, 's': cursor_ms - dur_cs * 10,
                          'e': cursor_ms})
    tail = strip_ass_overrides(text[KARAOKE_TAG_RE.search('', 0).end()
                                   if False else len(text):]).strip()
    if tail:
        words.append({'w': tail, 's': cursor_ms, 'e': cursor_ms})
    return words, cursor_ms


def parse_timestamp(ts: str) -> int:
    h, m, rest = ts.split(':')
    s, cs = rest.split('.')
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000
            + int(cs.ljust(2, '0')[:2]) * 10)


def parse_ass(path: Path) -> list[dict]:
    in_events = False
    format_keys: list[str] = []
    words: list[dict] = []
    with path.open('r', encoding='utf-8-sig', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\r\n')
            if line.startswith('[Events]'):
                in_events = True
                continue
            if in_events and line.startswith('['):
                in_events = False
            if not in_events:
                continue
            if line.startswith('Format:'):
                format_keys = [k.strip() for k in line.split(':', 1)[1].split(',')]
                continue
            if not (line.startswith('Dialogue:')
                    or line.startswith('Comment:')):
                continue
            kind, payload = line.split(':', 1)
            if kind.strip().lower() == 'comment':
                continue
            fields = payload.split(',', len(format_keys) - 1)
            row = dict(zip(format_keys, fields))
            text = row.get('Text', '').strip()
            if not text:
                continue
            try:
                start_ms = parse_timestamp(row['Start'])
                end_ms = parse_timestamp(row['End'])
            except (KeyError, ValueError):
                continue

            if KARAOKE_TAG_RE.search(text):
                ks, last_ms = split_karaoke(text, start_ms)
                if ks:
                    ks[-1]['e'] = max(ks[-1]['e'], end_ms)
                    words.extend(ks)
                else:
                    words.append({'w': strip_ass_overrides(text),
                                  's': start_ms, 'e': end_ms})
            else:
                plain = strip_ass_overrides(text).strip()
                if plain:
                    words.append({'w': plain, 's': start_ms, 'e': end_ms})
    return words


def build_ref(words: list[dict],
              source: str,
              url: str,
              duration_ms: int,
              language: str = 'en',
              samplerate: int = 16000) -> dict:
    norm = []
    p = 0
    for w in words:
        import re as _re
        w_norm = _re.sub(r'[^a-z0-9 ]+', '',
                         w['w'].lower().replace('-', ' ')).strip()
        if not w_norm:
            continue
        norm.append({'p': p, 's': int(w['s']), 'e': int(w['e']),
                     'w': w_norm})
        p += 1
    return {
        'source': source,
        'url': url,
        'duration_ms': duration_ms,
        'language': language,
        'samplerate': samplerate,
        'transcript': {'words': norm},
    }


def main():
    parser = argparse.ArgumentParser(
        description='Convert Aegisub karaoke ASS to ref.json '
                    '(Ground Truth format for eval_asr.py).'
    )
    parser.add_argument('ass', help='Path to hand-timed .ass file')
    parser.add_argument('--source', default='', help='Source video title')
    parser.add_argument('--url', default='', help='Source URL')
    parser.add_argument('--duration-ms', type=int, default=0,
                        help='Audio duration in ms (optional)')
    parser.add_argument('--language', default='en')
    parser.add_argument('--out', default=None,
                        help='Output JSON path (default: <ass>.ref.json)')
    args = parser.parse_args()

    ass_path = Path(args.ass)
    if not ass_path.is_file():
        sys.exit(f'ASS file not found: {ass_path}')

    words = parse_ass(ass_path)
    if not words:
        sys.exit('No words extracted. Check karaoke tags or line format.')

    ref = build_ref(words, args.source, args.url,
                    duration_ms=args.duration_ms,
                    language=args.language)

    out_path = Path(args.out) if args.out else ass_path.with_suffix('.ref.json')
    with out_path.open('w', encoding='utf-8') as f:
        json.dump(ref, f, ensure_ascii=False, indent=2)
    print(f'Wrote {len(ref["transcript"]["words"])} words to {out_path}')


if __name__ == '__main__':
    main()