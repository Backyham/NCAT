"""
Line-anchored evaluation: measure ASR word timestamps against ASS line GT.

For each ASR word, find which ASS line it belongs to (via text matching),
then compare the ASR word's start time to the ASS line's start time.
This treats ASS line-level timestamps as absolute ground truth.

Reports per-backend:
  - in_range_pct: % of ASR words whose timestamp falls within ANY
    ASS line's [start, end] range
  - line_anchored_mae_ms: mean |ASR word start - matched ASS line start|
  - line_anchored_end_mae_ms: mean |ASR word end - matched ASS line end|
  - matched: count of ASR words successfully matched to an ASS line
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_ass_lines(ass_path: str) -> list[tuple[int, int, str]]:
    """Return [(start_ms, end_ms, english_text), ...] from bilingual ASS."""
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
                out.append((_ass_ts_to_ms(start), _ass_ts_to_ms(end), en))
    return out


def _ass_ts_to_ms(ts: str) -> int:
    h, m, rest = ts.split(':')
    s, cs = rest.split('.')
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000
            + int(cs.ljust(2, '0')[:2]) * 10)


def normalize_word(w: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", '', w.lower().replace("'", '').strip())


def build_ass_word_index(ass_lines):
    """Return list of (norm_word, line_idx) in document order."""
    out = []
    for idx, (_s, _e, en) in enumerate(ass_lines):
        for w in en.split():
            nw = normalize_word(w)
            if nw:
                out.append((nw, idx))
    return out


def match_hyp_to_ass_lines(hyp_words: list[dict],
                           ass_lines,
                           ass_word_index,
                           max_time_dev_ms: int = 5000) -> list[dict]:
    """For each hyp word, find its expected ASS line and expected
    word position within that line (by sequential text matching).

    The "expected" position is computed as: divide the line's
    [start, end] evenly across the line's words, in document order.
    """
    # Build per-line word sequence
    line_words: list[list[str]] = []
    for _s, _e, en in ass_lines:
        ws = []
        for w in en.split():
            nw = normalize_word(w)
            if nw:
                ws.append(nw)
        line_words.append(ws)

    # Cursor per line: next expected word index
    cursors = [0] * len(ass_lines)

    out = []
    for hw in hyp_words:
        s, e = hw['s'], hw['e']
        nw = normalize_word(hw['w'])
        # Try to find the line that contains hyp's time AND expects this word
        matched_line = None
        for idx, (ls, le, _en) in enumerate(ass_lines):
            if not (ls <= s and e <= le):
                continue
            cur = cursors[idx]
            if cur < len(line_words[idx]) and line_words[idx][cur] == nw:
                matched_line = idx
                cursors[idx] = cur + 1
                break
        # Fallback: any line containing the time
        if matched_line is None:
            for idx, (ls, le, _en) in enumerate(ass_lines):
                if ls <= s and e <= le:
                    matched_line = idx
                    break
        # Fallback: closest line start within tolerance
        if matched_line is None:
            best_idx = -1
            best_dev = max_time_dev_ms + 1
            for idx, (ls, le, _en) in enumerate(ass_lines):
                dev = abs(ls - s)
                if dev < best_dev:
                    best_dev = dev
                    best_idx = idx
            if best_dev <= max_time_dev_ms:
                matched_line = best_idx
        if matched_line is None:
            out.append({
                'w': hw['w'], 's': s, 'e': e,
                'line_idx': -1,
                'line_start': -1, 'line_end': -1,
                'word_idx': -1, 'n_words': -1,
                'expected_s': -1, 'expected_e': -1,
                'matched': False,
            })
            continue
        ls, le, _ = ass_lines[matched_line]
        wi = cursors[matched_line]
        n_words = len(line_words[matched_line])
        # Evenly distribute words across [ls, le]
        if n_words > 0:
            slot = (le - ls) / n_words
            expected_s = int(ls + wi * slot)
            expected_e = int(ls + (wi + 1) * slot)
        else:
            expected_s = ls
            expected_e = le
        out.append({
            'w': hw['w'], 's': s, 'e': e,
            'line_idx': matched_line,
            'line_start': ls, 'line_end': le,
            'word_idx': wi, 'n_words': n_words,
            'expected_s': expected_s, 'expected_e': expected_e,
            'matched': True,
        })
        # Advance cursor for the next word
        cursors[matched_line] = min(wi + 1, n_words)
    return out


def summarize(matches, total_hyp: int, n_ass_words: int) -> dict:
    n_matched = sum(1 for m in matches if m['matched'])
    n_in_range = sum(
        1 for m in matches
        if m['matched'] and m['line_start'] <= m['s'] and m['e'] <= m['line_end']
    )
    # Signed errors (positive = ASR is LATER than expected)
    signed_start = [m['s'] - m['expected_s'] for m in matches if m['matched']]
    signed_end = [m['e'] - m['expected_e'] for m in matches if m['matched']]
    # Duration errors: hyp_dur - expected_dur (positive = ASR word too long)
    signed_dur = [
        (m['e'] - m['s']) - (m['expected_e'] - m['expected_s'])
        for m in matches if m['matched']
    ]
    if not signed_start:
        return {
            'matched_pct': 0.0, 'in_range_pct': 0.0,
            'n_matched': 0, 'n_in_range': 0,
        }

    def stats(xs):
        xs = list(xs)
        n = len(xs)
        mean = sum(xs) / n
        var = sum((x - mean) ** 2 for x in xs) / n
        std = var ** 0.5
        sxs = sorted(xs)
        return {
            'mean': mean, 'std': std, 'var': var,
            'median': sxs[n // 2],
            'q1': sxs[n // 4], 'q3': sxs[3 * n // 4],
            'min': sxs[0], 'max': sxs[-1], 'n': n,
        }

    abs_start = [abs(x) for x in signed_start]
    abs_end = [abs(x) for x in signed_end]
    abs_dur = [abs(x) for x in signed_dur]

    s_start = stats(signed_start)
    s_end = stats(signed_end)
    s_dur = stats(signed_dur)

    # MAE before any offset
    mae_start_before = sum(abs_start) / len(abs_start)
    mae_end_before = sum(abs_end) / len(abs_end)
    mae_dur_before = sum(abs_dur) / len(abs_dur)

    # Best offset to add to START times
    best_start_offset = -s_start['median']
    mae_start_after = sum(
        abs(x + best_start_offset) for x in signed_start
    ) / len(signed_start)

    # Best offset to add to END times
    best_end_offset = -s_end['median']
    mae_end_after = sum(
        abs(x + best_end_offset) for x in signed_end
    ) / len(signed_end)

    # Best joint offset: same offset applied to both start and end
    # (i.e., shifting the entire word by the same amount)
    # This is the most useful "single global shift" for both start and end.
    joint_offset = -((s_start['median'] + s_end['median']) / 2)
    mae_start_joint = sum(
        abs(x + joint_offset) for x in signed_start
    ) / len(signed_start)
    mae_end_joint = sum(
        abs(x + joint_offset) for x in signed_end
    ) / len(signed_end)

    # What if we correct start with one offset, end with another?
    mae_start_after_end = sum(
        abs(x + best_start_offset) for x in signed_start
    ) / len(signed_start)  # same as mae_start_after

    # Duration-based MAE: how much time on screen is off?
    # (use 2D shift: start +offset, end +offset, then duration is unchanged)

    return {
        'matched_pct': 100 * n_matched / total_hyp,
        'in_range_pct': 100 * n_in_range / total_hyp,
        'n_matched': n_matched,
        'n_in_range': n_in_range,
        'n_ass_words': n_ass_words,
        'n_hyp_words': total_hyp,
        # Three error types
        'start_bias_ms': s_start['mean'],
        'start_std_ms': s_start['std'],
        'end_bias_ms': s_end['mean'],
        'end_std_ms': s_end['std'],
        'duration_bias_ms': s_dur['mean'],
        'duration_std_ms': s_dur['std'],
        'start_stats': s_start,
        'end_stats': s_end,
        'dur_stats': s_dur,
        # MAE before any correction
        'mae_start_ms': mae_start_before,
        'mae_end_ms': mae_end_before,
        'mae_duration_ms': mae_dur_before,
        # Best individual offsets
        'best_start_offset_ms': best_start_offset,
        'best_end_offset_ms': best_end_offset,
        # MAE after per-side offset (hypothetical, 2 parameters)
        'mae_start_after_offset_ms': mae_start_after,
        'mae_end_after_offset_ms': mae_end_after,
        # MAE after joint offset (1 parameter, most practical)
        'joint_offset_ms': joint_offset,
        'mae_start_joint_ms': mae_start_joint,
        'mae_end_joint_ms': mae_end_joint,
        # Reductions
        'start_reduction_pct': 100 * (
            mae_start_before - mae_start_joint) / mae_start_before,
        'end_reduction_pct': 100 * (
            mae_end_before - mae_end_joint) / mae_end_before,
    }


def load_hyp(json_path: str) -> list[dict]:
    with open(json_path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    return d['transcript']['words']


def main():
    parser = argparse.ArgumentParser(
        description='Line-anchored eval: ASR word timestamps vs ASS line GT.'
    )
    parser.add_argument('ass', help='Bilingual ASS file (source of GT)')
    parser.add_argument('--hyp', nargs='+', required=True,
                        metavar='LABEL=JSON',
                        help='e.g. --hyp parakeet=p.json qwen=q.json')
    parser.add_argument('--max-dev-ms', type=int, default=5000)
    parser.add_argument('--report', default=None)
    args = parser.parse_args()

    if not Path(args.ass).is_file():
        sys.exit(f'ASS not found: {args.ass}')
    ass_lines = parse_ass_lines(args.ass)
    if not ass_lines:
        sys.exit('No English lines found in ASS.')
    ass_word_index = build_ass_word_index(ass_lines)
    n_ass_words = len(ass_word_index)
    print(f'ASS GT: {len(ass_lines)} lines, {n_ass_words} English words\n')

    results = []
    for spec in args.hyp:
        if '=' not in spec:
            sys.exit(f'--hyp expects LABEL=JSON, got: {spec}')
        label, json_path = spec.split('=', 1)
        if not Path(json_path).is_file():
            print(f'  {label}: JSON not found: {json_path}')
            continue
        hyp_words = load_hyp(json_path)
        matches = match_hyp_to_ass_lines(
            hyp_words, ass_lines, ass_word_index,
            max_time_dev_ms=args.max_dev_ms,
        )
        summary = summarize(matches, len(hyp_words), n_ass_words)
        summary['backend'] = label
        summary['n_hyp_words'] = len(hyp_words)
        results.append(summary)

    print('Per-backend signed-error analysis:')
    print(f'  {"Backend":<10} {"MAE_start":>10} {"Bias_start":>11} {"Std_start":>10} '
          f'{"MAE_end":>10} {"Bias_end":>11} {"Std_end":>10} '
          f'{"Bias_dur":>10} {"Std_dur":>9}')
    print('  ' + '-' * 110)
    for r in results:
        if r.get('mae_start_ms') is None:
            print(f'  {r["backend"]:<10} (no matches)')
            continue
        print(f'  {r["backend"]:<10} '
              f'{r["mae_start_ms"]:>8.1f}ms '
              f'{r["start_bias_ms"]:>+9.1f}ms {r["start_std_ms"]:>9.1f} '
              f'{r["mae_end_ms"]:>8.1f}ms '
              f'{r["end_bias_ms"]:>+9.1f}ms {r["end_std_ms"]:>9.1f} '
              f'{r["duration_bias_ms"]:>+8.1f}ms {r["duration_std_ms"]:>8.1f}')
    print('\n  Sign: positive = ASR is LATER (or word too LONG) than expected')
    print('  Bias_dur > 0 means ASR words are TOO LONG vs expected')
    print('  Bias_dur < 0 means ASR words are TOO SHORT')

    print('\nOffset analysis (single global time shift to ADD to BOTH start and end):')
    print(f'  {"Backend":<10} {"MAE_start_before":>16} {"Joint offset":>14} '
          f'{"MAE_start_after":>16} {"Reduction_start":>16}')
    print('  ' + '-' * 85)
    for r in results:
        if r.get('mae_start_ms') is None:
            continue
        print(f'  {r["backend"]:<10} '
              f'{r["mae_start_ms"]:>14.1f}ms '
              f'{r["joint_offset_ms"]:>+12.0f}ms '
              f'{r["mae_start_joint_ms"]:>14.1f}ms '
              f'{r["start_reduction_pct"]:>14.1f}%')
    print(f'  {"":10} {"MAE_end_before":>16} {"":14} '
          f'{"MAE_end_after":>16} {"Reduction_end":>16}')
    print('  ' + '-' * 85)
    for r in results:
        if r.get('mae_start_ms') is None:
            continue
        print(f'  {r["backend"]:<10} '
              f'{r["mae_end_ms"]:>14.1f}ms '
              f'{"":14} '
              f'{r["mae_end_joint_ms"]:>14.1f}ms '
              f'{r["end_reduction_pct"]:>14.1f}%')
    print('\n  Joint offset = -(median_start + median_end) / 2')
    print('  After this offset, both start and end are shifted by the same amount')
    print('  (ASR word durations remain unchanged, only their position in time moves)')

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f'\nReport: {args.report}')


if __name__ == '__main__':
    main()