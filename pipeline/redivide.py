"""Cut a continuous translation back onto the human's cue boundaries.

The MT stage is handed the reviewed text undivided, because a translator
working on a whole passage resolves register and terminology that a
translator working on one chopped line cannot. The cost is that its output
arrives as a continuous run of Chinese with no idea where the cues were.

Recovering the boundaries is a cross-lingual alignment problem, and it has to
be honest about how well it can be solved. English and Chinese share almost
no character content, so character-level matching is useless here. What is
reliable is *structure*: if the source is sent with one line per cue and the
model is asked to keep the line structure, the output lines map back
one-to-one. That is the path taken, and the line counts are checked rather
than assumed.

When the counts disagree -- the model merged two lines, or split one, or
dropped a cue -- the fallback allocates proportionally by source length and
snaps the cut to Chinese punctuation, and every affected cue is reported.
A wrong Chinese cue is worse than a flagged one, so nothing is silently
guessed.

Semantics do enter, but only in service of the snap: a cut is placed after
``。！？`` or ``，`` if one is near the target, never inside a punctuation
run, and never leaving a single trailing character on a cue.
"""
from __future__ import annotations

# Sentence-final and clause-final Chinese punctuation, best places to cut.
ZH_SENTENCE_END = '。！？…'
ZH_CLAUSE_END = '，、；：,;:'
# Never end a cue on one of these; they belong to the character before.
ZH_TRAILING = '。，、；：！？…”’』」）】'


def _split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace('\r\n', '\n').split('\n')]


def _snap(text: str, want: int) -> int:
    """Move a cut position ``want`` to a nearby acceptable boundary."""
    n = len(text)
    if n == 0:
        return 0
    want = max(0, min(n - 1, want))

    def score(pos: int) -> tuple[int, int]:
        # Prefer sentence end, then clause end, then any punctuation, and
        # penalise distance from the proportional target.
        if pos == 0:
            return (0, 0)
        prev = text[pos - 1]
        if prev in ZH_SENTENCE_END:
            rank = 0
        elif prev in ZH_CLAUSE_END:
            rank = 1
        elif prev in ZH_TRAILING:
            rank = 2
        else:
            rank = 3
        return (rank, abs(pos - want))

    best = min(range(max(0, want - 24), min(n, want + 25)), key=score,
               default=want)
    # Do not leave a single orphan character behind.
    while 0 < best < n - 1 and text[best - 1] in ZH_TRAILING:
        best -= 1
    return best


def _enforce_minimum(cuts: list[int], total: int, want: int,
                     floor: int = 4) -> list[int]:
    """Push cuts apart so no cue is shorter than ``floor`` characters.

    The proportional estimate can land two characters from the previous cut
    when the source lines are short, which yields a one-character cue
    ("你" | "好"). A one-character cue is not a subtitle cue, so cuts are
    walked forward until they clear the floor, then any inversion that
    creates is repaired.
    """
    out = list(cuts)
    for i in range(1, len(out)):
        if out[i] - out[i - 1] < floor:
            out[i] = min(out[i - 1] + floor, total)
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = min(out[i - 1] + 1, total)
    while len(out) > want:
        out.pop()
    while len(out) < want:
        out.append(total)
    return out


def _split_segments(zh: str) -> list[str]:
    """Split continuous Chinese at sentence-final punctuation, keeping it."""
    segs: list[str] = []
    cur = ''
    for ch in zh:
        cur += ch
        if ch in ZH_SENTENCE_END:
            segs.append(cur)
            cur = ''
    if cur.strip():
        segs.append(cur)
    return [s for s in segs if s.strip()]


def _assign(en_lines: list[str], segments: list[str]) -> list[str]:
    """Hand whole sentences to cues in proportion to their source length.

    Recovering sentences first and then distributing them is far better than
    slicing the string at estimated character offsets. When the model merged
    three cues into one line, splitting at Chinese full stops usually puts the
    boundaries back exactly; slicing at proportions produced one-character
    cues and empty ones.
    """
    weights = [max(len(x), 1) for x in en_lines]
    total_en = sum(weights) or 1
    total_zh = sum(len(s) for s in segments) or 1
    n = len(en_lines)

    out: list[str] = [''] * n
    si = 0
    used = 0
    cum = 0
    for i in range(n):
        if i == n - 1:
            out[i] = ''.join(segments[si:])
            break
        cum += weights[i]
        target = total_zh * (cum / total_en)
        while si < len(segments) and used < target:
            out[i] += segments[si]
            used += len(segments[si])
            si += 1
        if not out[i] and si < len(segments):
            out[i] = segments[si]
            used += len(segments[si])
            si += 1

    # A cue that ended up empty donates to its neighbour rather than shipping.
    for i in range(n):
        if out[i]:
            continue
        if i + 1 < n:
            out[i + 1] = out[i] + out[i + 1]
        elif i > 0:
            out[i - 1] += out[i]
    return out


def redivide(en_lines: list[str], zh_text: str, *,
             snap_window: int = 24) -> tuple[list[str], list[dict]]:
    """Split ``zh_text`` across ``en_lines``.

    Returns the per-cue Chinese and a list of notes describing any cue whose
    boundary is not certain. An empty note list means every boundary is exact.
    """
    if not en_lines:
        return [], []

    zh_lines = _split_lines(zh_text)

    # --- exact: structure preserved ------------------------------------
    if len(zh_lines) == len(en_lines):
        return list(zh_lines), []

    notes: list[dict] = [{
        'index': -1,
        'issue': 'line count mismatch',
        'detail': f'{len(en_lines)} source cues, {len(zh_lines)} output lines',
        'method': 'proportional',
    }]

    # If the model merely wrapped long lines, rejoin and try again.
    joined = ''.join(x for x in zh_lines if x)
    if not joined:
        return [''] * len(en_lines), notes

    # --- recovered: split sentences, then distribute across cues --------
    segments = _split_segments(joined)
    if segments:
        out = _assign(en_lines, segments)
        return out, notes

    # --- last resort: proportional slicing with punctuation snapping ----
    weights = [max(len(x), 1) for x in en_lines]
    total_en = sum(weights) or 1
    total_zh = len(joined)

    out: list[str] = []
    targets: list[int] = []
    cursor = 0
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            target = total_zh
        else:
            target = round(total_zh * (sum(weights[:i + 1]) / total_en))
            target = _snap(joined, target)
        targets.append(target)
    targets = _enforce_minimum(targets, total_zh, len(weights))

    for target in targets:
        target = max(target, cursor)
        out.append(joined[cursor:target].strip())
        cursor = target

    # Anything left over belongs to the final cue.
    if cursor < total_zh and out:
        out[-1] = (out[-1] + joined[cursor:]).strip()

    for i, piece in enumerate(out):
        if not piece:
            notes.append({'index': i, 'issue': 'empty cue',
                          'detail': 'no Chinese allocated', 'method': 'none'})
    return out, notes


def redivide_texts(en_text: str, zh_text: str) -> tuple[list[str], list[str],
                                                       list[dict]]:
    """Convenience wrapper taking the two continuous files as strings."""
    en_lines = [x for x in _split_lines(en_text) if x]
    zh_cues, notes = redivide(en_lines, zh_text)
    return en_lines, zh_cues, notes