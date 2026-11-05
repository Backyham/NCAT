"""Text normalisation shared by TM, terms, alignment and QA."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:['_-][A-Za-z0-9]+)*")


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalise_key(text: str) -> str:
    return normalise_text(text).casefold()


def source_hash(text: str) -> str:
    return hashlib.sha256(normalise_key(text).encode("utf-8")).hexdigest()


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def tokenize_source(text: str) -> list[str]:
    """Tokenise for alignment; CJK runs become one token."""
    text = normalise_text(text)
    tokens: list[str] = []
    buffer: list[str] = []
    for char in text:
        if _CJK.match(char):
            if buffer:
                tokens.extend(_LATIN_WORD.findall("".join(buffer)))
                buffer.clear()
            tokens.append(char)
        elif char.isalnum() or char in "'_-":
            buffer.append(char)
        else:
            if buffer:
                tokens.extend(_LATIN_WORD.findall("".join(buffer)))
                buffer.clear()
    if buffer:
        tokens.extend(_LATIN_WORD.findall("".join(buffer)))
    return [token.casefold() for token in tokens if token]


def cps(text: str, duration_ms: int) -> float | None:
    if not text or duration_ms <= 0:
        return None
    return len(text) / (duration_ms / 1000)
