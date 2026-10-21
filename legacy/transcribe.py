'''
NCAT transcribe dispatch (Parakeet-only).

Wraps asr_parakeet.parakeet_tdt to produce VoiceBase-style JSON
output (matching the historical Whisper-based schema) and a plain
.txt file. Sentence-tokenizes the text via nltk.

To re-enable Qwen3-ASR (or any other backend):
  1. Add an "x" key to avail_asr with a label + description.
  2. Add an `elif asr_sel == x:` branch below that imports the
     backend module and calls its top-level function.
  3. The backend's return must be (vbjson_dict, full_text) where
     vbjson_dict = {'transcript': {'words': [{p,s,e,w}, ...]}}.
'''
import json
import os
import sys

from nltk_func import nltk_proc as np

from exception_proc import show_exception_and_exit

sys.excepthook = show_exception_and_exit

avail_asr = {
    "7": ("Parakeet-TDT-0.6B-v3 (NVIDIA NeMo)",
          "Primary ASR. Apply +300ms offset via main.py --offset-ms."),
}


def transcribe_func(asr_sel: int, audio: str) -> int:
    output_filename = (
        '.'.join(audio.split('.')[0:-1])
        if len(audio.split('.')[0:-1]) > 1
        else audio.split('.')[0]
    )
    if asr_sel == 7:
        from asr_parakeet import parakeet_tdt
        result_json, full_text = parakeet_tdt(audio)
    else:
        raise ValueError(
            f'Unsupported ASR selection: {asr_sel}. '
            f'Available: {list(avail_asr.keys())}'
        )

    with open(output_filename + '.txt', 'w', encoding='utf-8') as f:
        f.write(np(full_text))
    with open(output_filename + '.json', 'w', encoding='utf-8') as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    os.remove(audio)
    return 0


if __name__ == '__main__':
    pass
