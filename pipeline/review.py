"""Stage 4: hand the ASR transcript to a human, with a cue division to argue with.

The pipeline used to segment automatically and translate whatever fell out.
That order is wrong for subtitle work: the recogniser's segmentation is a
guess, and feeding a guess to the MT model means the translator is paid to
repair someone else's bad line breaks. The human already has to read the
whole transcript anyway, because they have to correct the wording.

So this stage produces a *proposal*, not a result:

    <stem>.draft.txt        the recogniser's English text, one cue per line
    <stem>.reviewed.cues.json  the proposed cues, with timings and reasons

Both are meant to be edited. The reviewed text is what the MT stage consumes,
undivided; the cues are what the translation is later cut against. Nothing
downstream treats the proposal as authoritative.

Vocabulary, because "line" means too many things:

  **cue**    one unit of translated dialogue, and exactly one ASS *event*:
             one ``Dialogue:`` row with a start and an end, which appears
             and disappears as a unit.
  **English text**  the English payload of a cue. This is what the 75
             character limit applies to.

The Chinese side is produced later, by MT, and is not subject to this limit.

Boundaries are chosen on **syntax, not punctuation**. Splitting where a comma
happens to fall produces cues that read as fragments, because a comma is not
where a phrase ends. So cue boundaries come from NLTK's POS tagger, and a cue
is only ever cut where a noun, verb or pronoun can end a phrase and another
can begin one. Tagger availability is optional: without it the proposer
degrades to punctuation and says so, rather than failing or pretending.

Every cue records *why* it ended, so review is a matter of disagreeing with
specific decisions rather than reverse-engineering the script.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .paths import JobPaths, Report, build_paths, noop_report

# The English payload of a cue is capped at this many characters.
DEFAULT_MAX_CHARS = 75
DEFAULT_GAP_MS = 600
DEFAULT_MAX_SECONDS = 7.0
DEFAULT_MAX_CPS = 21.0          # English reading-speed ceiling
# A pause on its own is not a boundary. Without a floor, any hesitation
# inside a sentence splits it: "I think this is" / "wrong." reads as two cues
# because that is what it becomes.
DEFAULT_MIN_CHARS = 14

# Sentence-final punctuation: a cue should end here if it can.
SENTENCE_END = re.compile(r'[.!?…]["\')\]]?$')
# Clause-final punctuation: acceptable, but a weaker boundary.
CLAUSE_END = re.compile(r'[,;:—]["\')\]]?$')
# Words that must not be left dangling at the start of a cue. Breaking
# after one of these produces a fragment that reads as its own sentence.
NO_LEAD = {
    'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'from', 'he', 'her',
    'his', 'i', 'if', 'in', 'is', 'it', 'its', 'my', 'nor', 'of', 'on', 'or',
    'our', 'she', 'so', 'that', 'the', 'their', 'them', 'they', 'this', 'to',
    'up', 'was', 'we', 'were', 'what', 'when', 'which', 'who', 'will',
    'with', 'you', 'your',
}

# Penn Treebank tags after which a phrase has syntactically finished.
#
# Deliberately narrow. A wider set also allowed RB and IN, which produced
# cuts like "very | hot" and "too | when" -- formally legal, since an adverb
# may precede an adjective, but they split an adjective phrase in half.
# Nouns, verbs and pronouns are the boundaries that survive review.
VALID_CUT_BEFORE = {
    'NN', 'NNS', 'NNP', 'NNPS',
    'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ',
    'PRP', 'PRP$',
}

_TAGGER_READY: bool | None = None


def tagger_available() -> bool:
    """Whether the NLTK tagger can actually run, cached after the first try.

    Importing NLTK is not enough: its models live outside the venv, and a
    fresh machine will have the package without the data. Asking once and
    caching keeps the per-cue cost at zero.
    """
    global _TAGGER_READY
    if _TAGGER_READY is None:
        try:
            import nltk
            nltk.pos_tag(['test'])
            _TAGGER_READY = True
        except Exception:
            _TAGGER_READY = False
    return _TAGGER_READY


def _words_from(transcript_json: str | Path) -> list[dict]:
    data = json.loads(Path(transcript_json).read_text(encoding='utf-8'))
    return (data.get('transcript') or {}).get('words') or []


def _norm(word: str) -> str:
    return re.sub(r'[^a-z0-9]', '', word.lower())


def _align_tags(tokens: list[str], tagged: list[tuple[str, str]]) -> list[str]:
    """Map POS tags back onto the original word list.

    The tagger and the recogniser disagree about punctuation:
    ``word_tokenize`` splits ``"coffee,"`` into ``"coffee"`` and ``","``,
    while the ASR word list keeps them together. Matching on the raw strings
    therefore fails at the first comma, the whole tag stream is discarded,
    and the proposer silently falls back to a path that never cuts.
    Comparison is done on an alphanumeric-only form so the two line up.
    """
    norm = [_norm(w) for w in tokens]
    tags: list[str] = []
    cursor = 0
    for token, tag in tagged:
        key = _norm(token)
        if not key:
            continue                      # punctuation carries no position
        found = -1
        for k in range(cursor, len(norm)):
            if norm[k] == key:
                found = k
                break
        if found < 0:
            return []
        cursor = found + 1
        tags.append(tag)
    return tags if len(tags) == len(norm) else []


def propose(words: list[dict], *,
            max_chars: int = DEFAULT_MAX_CHARS,
            gap_ms: int = DEFAULT_GAP_MS,
            max_seconds: float = DEFAULT_MAX_SECONDS,
            max_cps: float = DEFAULT_MAX_CPS,
            min_chars: int = DEFAULT_MIN_CHARS) -> list[dict]:
    """Group word timings into a cue proposal.

    A cue is closed when the speaker pauses at a place the syntax agrees with,
    when it is too fast to read, when it outlasts the duration limit, or when
    its English text would exceed ``max_chars``. The length rule applies to
    the cue's English text as a whole -- not to a wrapped rendering of it,
    because ASS performs no automatic wrapping and a prediction the renderer
    ignores would be worse than no prediction at all.

    Returns dicts with the English text, the timings, and the reason the cue
    ended, which is what a reviewer needs in order to disagree usefully.
    """
    cues: list[dict] = []
    current: list[dict] = []

    def flush(reason: str) -> None:
        if not current:
            return
        words_in = list(current)
        text = ' '.join(w['w'] for w in words_in).strip()
        start, end = int(words_in[0]['s']), int(words_in[-1]['e'])
        dur = max((end - start) / 1000.0, 0.001)
        cues.append({
            'index': len(cues),
            'start_ms': start,
            'end_ms': end,
            'en': text,
            'reason': reason,
            'chars': len(text),
            'cps': round(len(text) / dur, 1),
            'needs_timing': False,
            'status': 'draft',
        })
        current.clear()

    for word in words:
        w = word['w']
        if current:
            prev = current[-1]
            gap = int(word['s']) - int(prev['e'])
            text = ' '.join(x['w'] for x in current)
            span = (int(current[-1]['e']) - int(current[0]['s'])) / 1000.0
            over_chars = len(text) + 1 + len(w) > max_chars
            too_slow = span + gap / 1000.0 > max_seconds
            too_fast = (len(text) / max(span, 0.001) > max_cps
                        and SENTENCE_END.search(text))

            if gap >= gap_ms and _breaks_sentence(text, w, min_chars):
                flush(f'pause {gap}ms')
            elif too_fast:
                flush('reading speed')
            elif too_slow or over_chars:
                cut = _best_cut(current, text, max_chars)
                if cut is not None and 0 < cut < len(current):
                    tail = current[cut:]
                    del current[cut:]
                    flush(_reason(text, tail[0]))
                    current.extend(tail)
        current.append(word)
    flush('end')

    cues = _rebalance(cues, max_chars)
    for i, cue in enumerate(cues):
        cue['index'] = i
    return cues


def _breaks_sentence(text: str, next_word: str, min_chars: int) -> bool:
    """A pause is a boundary only where the syntax agrees."""
    if len(text) < min_chars:
        return False
    return bool(SENTENCE_END.search(text) or CLAUSE_END.search(text)
                or next_word[:1].isupper())


def _reason(text: str, next_word: dict | None) -> str:
    if text and SENTENCE_END.search(text):
        return 'sentence'
    if next_word is not None:
        return 'length'
    return 'end'


def _best_cut(current: list[dict], text: str, max_chars: int) -> int | None:
    """Index to cut ``current`` at, using the tagger where available.

    The preferred cut is the **latest** acceptable boundary that still fits
    the budget. An earlier version aimed for half the budget instead, which
    filled only part of it and left a spread of cues from 14 to 75
    characters: the short ones are unreadable fragments and they drag the
    reading speed up, because the same audio is now carrying more cues.

    Falls back to the nearest comma when the tagger is unavailable. When
    neither yields anything, a mid-point cut is used: returning None lets an
    unbroken run of speech grow without limit, which produced a single
    1,110-character cue over a three-minute outro. An imperfect split is
    recoverable by a reviewer; a cue that violates the limit is not
    obviously broken to them.
    """
    if len(text) <= max_chars * 0.7:
        # Nothing is seriously over budget; splitting here would only create
        # an orphan cue after it.
        return None

    tokens = [w['w'] for w in current]

    def fits(i: int) -> bool:
        return len(' '.join(tokens[:i])) <= max_chars

    if tagger_available():
        try:
            import nltk
            tagged = nltk.pos_tag(nltk.word_tokenize(' '.join(tokens)))
            tags = _align_tags(tokens, tagged)
            if tags:
                for i in range(len(tokens) - 1, 0, -1):
                    if tags[i - 1] not in VALID_CUT_BEFORE:
                        continue
                    if tokens[i].lower() in NO_LEAD:
                        continue
                    if fits(i):
                        return i
        except Exception:
            pass

    for i in range(len(tokens) - 1, 0, -1):
        if CLAUSE_END.search(tokens[i]) and fits(i):
            return i + 1

    # Last resort: the latest word that still fits.
    for i in range(len(tokens) - 1, 0, -1):
        if fits(i):
            return i
    return None


def _rebalance(cues: list[dict], max_chars: int) -> list[dict]:
    """Repair the two things a purely mechanical split gets wrong.

    A cue that opens on a dangling function word reads as its own sentence,
    so it is pulled back into the previous cue -- unless the previous cue
    already ended a sentence, where "And we should go." is legitimately its
    own cue rather than a fragment.

    A cue of one or two words is not worth displaying on its own, so it is
    merged forward when the pair still fits inside the character budget.
    """
    out: list[dict] = []

    for cue in cues:
        head = cue['en'].split()
        prev = out[-1] if out else None

        merge_in = (
            prev is not None
            and head
            and head[0].lower() in NO_LEAD
            and len(prev['en'].split()) > 1
            and not SENTENCE_END.search(prev['en'])
            and len(prev['en']) + 1 + len(cue['en']) <= max_chars
        )
        if merge_in:
            tail = cue['en']
            # The merged cue now opens a fresh sentence, so the connector is
            # capitalised rather than left stranded mid-sentence.
            prev['en'] = f"{prev['en']} {tail[0].upper()}{tail[1:]}"
            prev['end_ms'] = cue['end_ms']
            _refresh(prev)
            continue

        merge_forward = (
            prev is not None
            and len(head) <= 2
            and len(prev['en']) + 1 + len(cue['en']) <= max_chars
        )
        if merge_forward:
            prev['en'] = f"{prev['en']} {cue['en']}"
            prev['end_ms'] = cue['end_ms']
            _refresh(prev)
            continue

        out.append(cue)

    return out


def _refresh(cue: dict) -> None:
    dur = max((cue['end_ms'] - cue['start_ms']) / 1000.0, 0.001)
    cue['chars'] = len(cue['en'])
    cue['cps'] = round(len(cue['en']) / dur, 1)


def write_packet(paths: JobPaths, cues: list[dict], draft: str) -> None:
    paths.require('asr_draft_txt', 'reviewed_cues')
    Path(paths.asr_draft_txt).write_text(draft, encoding='utf-8')
    payload = {
        'note': 'proposal for review; edit freely. one cue per line, in order. '
                'the MT stage is given this text undivided.',
        'limits': {
            'max_english_chars': DEFAULT_MAX_CHARS,
            'max_cps': DEFAULT_MAX_CPS,
        },
        'counts': {
            'cues': len(cues),
            'words': sum(len(c['en'].split()) for c in cues),
        },
        'cues': cues,
    }
    Path(paths.reviewed_cues).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def review_stage(job_id: int = 0,
                 report: Report = noop_report,
                 paths: JobPaths | None = None,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 gap_ms: int = DEFAULT_GAP_MS,
                 max_seconds: float = DEFAULT_MAX_SECONDS,
                 max_cps: float = DEFAULT_MAX_CPS,
                 **_ignored) -> dict:
    if paths is None:
        raise ValueError('review stage requires paths')
    paths.require('transcript_json', 'transcript_txt')
    words = _words_from(paths.transcript_json)
    if not words:
        raise ValueError('transcript has no word timings to propose cues from')

    cues = propose(words, gap_ms=gap_ms, max_chars=max_chars,
                   max_cps=max_cps, max_seconds=max_seconds)
    draft = Path(paths.transcript_txt).read_text(encoding='utf-8')
    write_packet(paths, cues, draft)

    if tagger_available():
        report('cue boundaries chosen with the NLTK POS tagger')
    else:
        report('WARNING: the NLTK tagger is unavailable, so boundaries fell '
               'back to punctuation and are only approximate.')
        report('  NLTK_ALLOW_PROXIED_URLOPEN=1 python -c "import nltk; '
               'nltk.download(\'punkt_tab\'); '
               'nltk.download(\'averaged_perceptron_tagger_eng\')"')

    over = [c for c in cues if len(c['en']) > max_chars]
    if over:
        report(f'WARNING: {len(over)} cues exceed {max_chars} English '
               f'characters and need re-cutting by hand')

    fastest = max((c['cps'] for c in cues), default=0)
    too_fast = [c for c in cues if c['cps'] > max_cps]
    if too_fast:
        # Left in place deliberately: the speed ceiling is never honoured by
        # cutting mid-sentence, because a fast whole sentence beats a slow
        # fragment. The reviewer decides.
        report(f'WARNING: {len(too_fast)} cues exceed {max_cps} cps (fastest '
               f'{fastest}) and were left whole rather than split mid-sentence')
    report(f'proposed {len(cues)} cues from {len(words)} words')
    report(f'edit {Path(paths.asr_draft_txt).name}, then continue')
    return {'cues': len(cues), 'words': len(words), 'max_cps': fastest,
            'tagger': tagger_available(), 'over_max_chars': len(over),
            'over_cps': len(too_fast)}
