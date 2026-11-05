"""Align source text to ASR word timings.

This is a port of ``Full-auto_M134A16.py``'s LCS -> LIS -> regularize
pipeline. The important part is ``regularize``: it fills the gaps between
matched words instead of dropping every unmatched token, so a human edit such
as "um" or a repeated word does not invalidate a whole cue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .text import tokenize_source
from .transcript import normalise_word
from .types import Alignment, Cue, Word


def greedy_lcs_positions(source: Sequence[str],
                         target: Sequence[str]) -> list[int]:
    """The old greedy LCS: one target position (or -1) per source token."""
    n, m = len(source), len(target)
    if n == 0:
        return []
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    best = [(0, -1)] * n
    for i in range(n):
        for j in range(m):
            if source[i] == target[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
                if dp[i][j] + 1 > best[i][0]:
                    best[i] = (dp[i][j] + 1, j)
            else:
                dp[i + 1][j + 1] = max(dp[i + 1][j], dp[i][j + 1])
    return [position for _length, position in best]


def longest_increasing_run(positions: Sequence[int]) -> list[tuple[int, ...]]:
    """Keep a strictly increasing subsequence; unmatched entries are empty."""
    n = len(positions)
    if n == 0:
        return []
    dp = [1] * n
    previous = [-1] * n
    result: list[tuple[int, ...]] = [()] * n
    for i in range(1, n):
        best_length = 0
        best_previous = -1
        for j in range(i):
            if positions[j] < positions[i] and dp[j] > best_length:
                best_length = dp[j]
                best_previous = j
        dp[i] = best_length + 1
        previous[i] = best_previous
    last = dp.index(max(dp))
    result[last] = (positions[last],)
    while previous[last] != -1:
        last = previous[last]
        result[last] = (positions[last],)
    return result


def regularize(runs: list[tuple[int, ...]], total: int) -> list[tuple[int, ...]]:
    """Fill unmatched runs with an even share of the target word range."""
    n = len(runs)
    if n == 0:
        return []
    mismatched: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if runs[i] != ():
            i += 1
            continue
        j = i
        while j < n and runs[j] == ():
            j += 1
        mismatched.append((i, j))
        i = j

    for left, right in mismatched:
        if left > 0:
            start = runs[left - 1][0] + 1
        else:
            start = 0
        if right < n:
            end = runs[right][0]
        else:
            end = total
        if start + 1 >= end:
            for index in range(left, right):
                runs[index] = (start,)
        else:
            average = (end - start) / (right - left)
            cursor = start
            for index in range(left, right - 1):
                runs[index] = tuple(range(round(cursor),
                                          round(cursor + average)))
                cursor += average
            runs[right - 1] = tuple(range(round(cursor), end))
    return runs


def align_cues(cues: Sequence[Cue], words: Sequence[Word], *,
               method: str = "lcs_lis_regularize") -> list[Alignment]:
    if not cues:
        return []
    per_cue_tokens = [tokenize_source(cue.source_text) for cue in cues]
    source_tokens = [token for tokens in per_cue_tokens for token in tokens]
    target_tokens = [
        word.normalized or normalise_word(word.text) for word in words
    ]
    if not source_tokens or not target_tokens:
        return [
            Alignment(cue_id=cue.id, method=method, needs_timing=True)
            for cue in cues
        ]

    positions = greedy_lcs_positions(source_tokens, target_tokens)
    runs = regularize(longest_increasing_run(positions), len(target_tokens))

    alignments: list[Alignment] = []
    cursor = 0
    for cue, tokens in zip(cues, per_cue_tokens):
        count = len(tokens)
        cue_runs = runs[cursor:cursor + count]
        cursor += count
        non_empty = [run for run in cue_runs if run]
        if not non_empty:
            alignments.append(Alignment(
                cue_id=cue.id, method=method, needs_timing=True))
            continue
        first = non_empty[0][0]
        last = non_empty[-1][-1]
        if first < 0 or last < 0 or first >= len(words) or last >= len(words):
            alignments.append(Alignment(
                cue_id=cue.id, method=method, needs_timing=True))
            continue
        matched = sum(1 for run in cue_runs if run)
        confidence = matched / count if count else 0.0
        alignments.append(Alignment(
            cue_id=cue.id,
            first_word=first,
            last_word=last,
            start_ms=words[first].start_ms,
            end_ms=words[last].end_ms,
            method=method,
            confidence=round(confidence, 3),
            needs_timing=False,
        ))
    return alignments
