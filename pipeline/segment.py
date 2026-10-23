"""Segment stage: word timings -> bilingual cues.

Extracted from ``Full-auto_M134A16.py``. The alignment algorithm (greedy
leftmost LCS followed by an LIS monotonicity filter) is preserved exactly:
the human English line in a bilingual script is the ASR output re-filled by
hand, so it differs from the recognition in small ways (word swaps,
duplicates, a dropped filler), and the old code's comments explain why the
textbook LCS backtrack was not used. What changed is that this module has a
narrow job -- turn (transcript, bilingual source) into cues -- and delegates
ASS formatting to the style stage.

Timing model:
  start_ms = start of the first aligned ASR word
  end_ms   = end of the last aligned ASR word
When a line cannot be anchored, ``needs_timing`` is set and the times are
left at 0. The old code wrote ``0:00:00.00`` as a sentinel that the human
editor recognised; carrying a boolean is the same contract without the
magic value leaking into the database.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import JobPaths, Report, noop_report

# ASS timestamps are centisecond-precision: h:mm:ss.cc
ASS_TIME_RE = re.compile(r'(\d+):(\d{2}):(\d{2})[.](\d{2})')


def ms_to_ass(ms: int) -> str:
    ms = max(0, int(ms))
    return f'{ms // 3600000}:{(ms % 3600000) // 60000:02d}:' \
           f'{(ms % 60000) // 1000:02d}.{(ms % 1000) // 10:02d}'


def ass_to_ms(text: str) -> int:
    m = ASS_TIME_RE.fullmatch(text.strip())
    if not m:
        return 0
    h, mnt, s, cs = (int(g) for g in m.groups())
    return ((h * 60 + mnt) * 60 + s) * 1000 + cs * 10


def normalise(word: str) -> str:
    return re.sub(r'[^a-z0-9 ]+', '', word.lower().replace('-', ' '))


# --------------------------------------------------------------- alignment --

def greedy_lcs_positions(source: list[str],
                         target: list[str]) -> list[int]:
    """Map each token of ``source`` to a position in ``target``.

    Greedy leftmost rather than textbook backtrack, because ASR output has
    stutters ("i i i want to") that should distribute across duplicates
    instead of collapsing onto the first match and pushing later words out
    of order. Returns -1 where a token found no anchor; the caller filters
    non-monotonic entries via ``longest_increasing_run``.
    """
    n, m = len(source), len(target)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    best: list[tuple[int, int]] = [(0, -1)] * n
    for i in range(n):
        for j in range(m):
            if source[i] == target[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
                if dp[i][j] + 1 > best[i][0]:
                    best[i] = (dp[i][j] + 1, j)
            else:
                dp[i + 1][j + 1] = max(dp[i + 1][j], dp[i][j + 1])
    return [pos for _, pos in best]


def longest_increasing_run(positions: list[int]) -> list[int]:
    """Keep the longest strictly increasing subsequence of positions.

    Hand-edited English reorders words, which yields non-monotonic matches;
    keeping them would place a line's end before its start. Ties favour
    earlier positions, which matters when duplicates are involved.
    """
    if not positions:
        return []
    length = [1] * len(positions)
    prev = [-1] * len(positions)
    for i in range(1, len(positions)):
        best_len, best_prev = 0, -1
        for j in range(i):
            if positions[j] < positions[i] and length[j] > best_len:
                best_len, best_prev = length[j], j
        length[i] = best_len + 1
        prev[i] = best_prev
    end = max(range(len(positions)), key=lambda i: length[i])
    kept = [-1] * len(positions)
    while end != -1:
        kept[end] = positions[end]
        end = prev[end]
    return kept


# ------------------------------------------------------------ source parsing --

def is_cjk(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff'


def looks_chinese(line: str) -> bool:
    return any(is_cjk(ch) for ch in line)


def classify(line: str) -> str:
    """'zh' | 'en' | 'note', mirroring the original script's judgeType()."""
    stripped = line.strip()
    if not stripped:
        return 'note'
    if stripped[:9].lower() == 'ltt intro' or stripped[:8].lower() == 'lttintro':
        return 'note'
    if '\u203b' in stripped:            # ※ annotation marker
        return 'note'
    if re.match(r'[（(]*轴[：:] *', stripped) or stripped[:2] == '//':
        return 'note'
    return 'zh' if looks_chinese(stripped) else 'en'


def parse_bilingual_source(text: str) -> list[tuple[str, str]]:
    """Pair zh and en lines from the bilingual source file.

    The source format is the Nixiesubs convention: a Chinese line followed
    by its English line. Consecutive English lines attach to the preceding
    Chinese one; a note line stands alone.
    """
    pairs: list[tuple[str, str]] = []
    pending_zh: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        kind = classify(line)
        if kind == 'zh':
            if pending_zh is not None:
                pairs.append((pending_zh, ''))
            pending_zh = line
        elif kind == 'en':
            if pending_zh is None:
                pairs.append(('', line))
            else:
                pairs.append((pending_zh, line))
                pending_zh = None
    if pending_zh is not None:
        pairs.append((pending_zh, ''))
    return pairs


# ------------------------------------------------------------------ stage --

def build_cues(transcript_json: str | Path,
               bilingual_txt: str | Path,
               report: Report = noop_report) -> list[dict]:
    """Produce cue dicts from ASR word timings and bilingual source text."""
    data = json.loads(Path(transcript_json).read_text(encoding='utf-8'))
    words = (data.get('transcript') or {}).get('words') or []
    words = [w for w in words if w.get('w') != '.']
    recognised = [normalise(w['w']) for w in words]

    pairs = parse_bilingual_source(Path(bilingual_txt).read_text(encoding='utf-8'))
    report(f'{len(pairs)} source lines, {len(words)} recognised words')

    # Concatenate the English side; remember which word belongs to which
    # line so timing can be sliced back out per line.
    line_tokens: list[list[str]] = []
    flat: list[str] = []
    for _, en in pairs:
        tokens = normalise(en).split()
        line_tokens.append(tokens)
        flat.extend(tokens)

    anchors = longest_increasing_run(greedy_lcs_positions(flat, recognised))
    report(f'{sum(1 for a in anchors if a >= 0)}/{len(anchors)} tokens anchored')

    cues: list[dict] = []
    cursor = 0
    for index, ((zh, en), tokens) in enumerate(zip(pairs, line_tokens)):
        start_ms = end_ms = 0
        needs_timing = False
        asr_text = ''
        if tokens:
            first, last = cursor, cursor + len(tokens) - 1
            head = anchors[first] if first < len(anchors) else -1
            tail = anchors[last] if last < len(anchors) else -1
            if head >= 0 and tail >= 0 and head < len(words) and tail < len(words):
                start_ms = int(words[head]['s'])
                end_ms = int(words[tail]['e'])
                asr_text = ' '.join(w['w'] for w in words[head:tail + 1]).strip()
            else:
                needs_timing = True
            cursor = last + 1
        cues.append({
            'index': index,
            'start_ms': start_ms,
            'end_ms': end_ms,
            'zh': zh,
            'en': en,
            # What the recogniser actually heard for this line, sliced out of
            # the same anchored span. Without this the cue carries only the
            # human source text, and feeding it to the MT stage would quietly
            # evaluate the translation of a perfect transcript rather than of
            # what the pipeline will actually have to translate.
            'asr_text': asr_text,
            'status': 'draft',
            'needs_timing': needs_timing,
        })

    unaligned = sum(1 for c in cues if c['needs_timing'])
    report(f'{len(cues)} cues, {unaligned} need manual timing')
    return cues


def segment(job_id: int,
            paths: JobPaths | None = None,
            report: Report = noop_report,
            **_ignored) -> dict:
    if paths is None:
        raise ValueError('segment stage requires paths')
    paths.require('transcript_json', 'segments_txt', 'cues_json')

    if not Path(paths.transcript_json).is_file():
        raise FileNotFoundError(f'transcript missing: {paths.transcript_json}')
    if not Path(paths.segments_txt).is_file():
        raise FileNotFoundError(
            f'bilingual source missing: {paths.segments_txt}\n'
            'Write the zh/en lines there, one language per line, '
            'zh first, then re-run this stage.'
        )

    cues = build_cues(paths.transcript_json, paths.segments_txt, report)
    Path(paths.cues_json).write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'cues': len(cues),
            'needs_timing': sum(1 for c in cues if c['needs_timing'])}
