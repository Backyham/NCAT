"""whisper.cpp backend via its JSON output."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..transcript import transcript_from_words, word_from_voicebase
from ..types import Transcript, Word
from .base import ASRBackend, ASRCapabilities, ASRRequest, ASRError


class WhisperCppBackend(ASRBackend):
    name = "whisper_cpp"
    capabilities = ASRCapabilities(
        word_timestamps=True, confidence=False, local=True,
    )

    def transcribe(self, request: ASRRequest) -> Transcript:
        options = dict(request.options)
        binary = str(options.pop("binary", "")
                     or os.environ.get("NCAT_WHISPER_CPP", "")
                     or "whisper.cpp/main")
        model = str(options.pop("model", "")
                    or os.environ.get("NCAT_WHISPER_CPP_MODEL", ""))
        if not model:
            raise ASRError("whisper.cpp model path is required "
                           "(options.model or NCAT_WHISPER_CPP_MODEL)")
        if not (Path(binary).is_file() or shutil.which(binary)):
            raise ASRError(f"whisper.cpp binary not found: {binary}")
        if not Path(model).is_file():
            raise ASRError(f"whisper.cpp model not found: {model}")
        language = request.language or str(options.pop("language", "en"))
        audio = Path(request.audio_path)
        output = Path(str(audio) + ".json")
        if output.exists():
            output.unlink()
        command = [
            binary, "-m", model, "-l", language, "-ojf", "-f", str(audio),
        ]
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ASRError(
                f"whisper.cpp failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        finally:
            output.unlink(missing_ok=True)

        words: list[Word] = []
        texts: list[str] = []
        for segment in payload.get("transcription", []):
            texts.append(str(segment.get("text", "")))
            for token in segment.get("tokens", []):
                offsets = token.get("offsets", {})
                word = word_from_voicebase({
                    "p": len(words),
                    "s": offsets.get("from", 0),
                    "e": offsets.get("to", 0),
                    "w": token.get("text", ""),
                })
                if word is not None:
                    word.index = len(words)
                    words.append(word)
        text = " ".join(part.strip() for part in texts if part.strip())
        return transcript_from_words(
            words,
            source_id=str(options.get("source_id", "")),
            language=language,
            backend=self.name,
            model=model,
            text=text,
            duration_ms=words[-1].end_ms if words else None,
        )
