"""MT stage: translate cue source text to the target language.

The MT model itself lives behind an HTTP boundary rather than being
imported here. Two reasons:

* llama-cpp-python must be built for sm_61 CUDA, which is a different
  build from everything else in the app and lives in its own venv;
* keeping the model behind HTTP means a 30-minute batch can run in a
  separate process without the web worker holding the GPU.

``MT_BASE_URL`` points at that service (mt_serve, refactored for Linux).
Term substitution from the active glossary happens before the model call so
that "probe" is already "对刀探针" by the time the model sees the line.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import JobPaths, Report, noop_report

DEFAULT_MT_URL = os.environ.get('NCAT_MT_URL', 'http://127.0.0.1:8088')


class TranslateError(RuntimeError):
    pass


def load_cues(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('cues', [])
    return list(data)


def save_cues(path: str | Path, cues: list[dict]) -> None:
    Path(path).write_text(
        json.dumps(cues, ensure_ascii=False, indent=2), encoding='utf-8')


def _client():
    try:
        import httpx
    except ImportError as exc:
        raise TranslateError(
            'httpx is required for the MT stage; install the app deps'
        ) from exc
    # The MT service is local. Any HTTP(S)_PROXY in the environment would
    # otherwise be honoured and route this request to the proxy, which
    # answers 502 for a LAN address; trust_env=False disables that.
    return httpx.Client(base_url=DEFAULT_MT_URL, timeout=120.0,
                        trust_env=False)


def _health(client) -> dict:
    response = client.get('/health')
    response.raise_for_status()
    return response.json()


def load_model(client, name: str) -> None:
    payload = _health(client)
    if payload.get('model_loaded') == name:
        return
    response = client.post(f'/load/{name}')
    if response.status_code >= 400:
        raise TranslateError(
            f'model {name!r} failed to load: {response.text[:400]}')
    report_line = f'model loaded: {name}'


def report_line(message: str) -> None:
    print(message, flush=True)


def apply_glossary(text: str, terms: list[dict]) -> str:
    """Substitute confirmed glossary terms before handing text to the model.

    Longest source first, so "tool changer" wins over "changer". Matching is
    case-insensitive on word boundaries; CJK targets need no word boundary.
    """
    import re
    ordered = sorted(terms, key=lambda t: len(t.get('source', '')), reverse=True)
    for term in ordered:
        source = term.get('source')
        target = term.get('target')
        if not source or not target or not term.get('confirmed', True):
            continue
        pattern = re.escape(source.strip())
        # \b does not work next to CJK, so only assert boundaries for ASCII.
        left = r'(?<![A-Za-z0-9])' if source[:1].isascii() else ''
        right = r'(?![A-Za-z0-9])' if source[-1:].isascii() else ''
        text = re.sub(f'{left}{pattern}{right}',
                      target.replace('\\', r'\\'), text, flags=re.IGNORECASE)
    return text


def translate_cues(job_id: int,
                   paths: JobPaths | None = None,
                   report: Report = noop_report,
                   model: str | None = None,
                   target_lang: str = 'zh',
                   terms: list[dict] | None = None,
                   max_new_tokens: int = 512,
                   **_ignored) -> dict:
    """Translate every cue's ``en`` into ``zh``.

    ``model`` is required rather than defaulted. Two MT models are under
    evaluation and neither has been chosen, so there is no correct value to
    bake in; passing one explicitly keeps that decision visible.

    Returns a small summary; the mutated cues are written back to
    ``paths.cues_json`` so later stages and the editor see the result.
    """
    if paths is None:
        raise TranslateError('translate stage requires paths')
    if not model:
        raise TranslateError(
            'no MT model selected. Both registered candidates are still '
            'under evaluation, so none is a default; pass --mt-model '
            '(see GET /models on the MT service for the available names).'
        )
    paths.require('cues_json')
    if not Path(paths.cues_json).is_file():
        raise TranslateError(f'cue file missing: {paths.cues_json}')

    cues = load_cues(paths.cues_json)
    report(f'{len(cues)} cues to translate into {target_lang}')

    done = 0
    with _client() as client:
        load_model(client, model)
        for index, cue in enumerate(cues, 1):
            source_text = (cue.get('en') or '').strip()
            if not source_text:
                cue['zh'] = ''
            elif terms:
                source_text = apply_glossary(source_text, terms)
                cue['zh'] = source_text
            else:
                response = client.post('/translate', json={
                    'text': source_text,
                    'source_lang': 'en',
                    'target_lang': target_lang,
                    'max_new_tokens': max_new_tokens,
                })
                if response.status_code >= 400:
                    raise TranslateError(
                        f'cue {index} failed: {response.text[:300]}')
                cue['zh'] = response.json().get('translation', '')
            done += 1
            if done % 20 == 0 or done == len(cues):
                report(f'  translated {done}/{len(cues)}')
            save_cues(paths.cues_json, cues)

    return {'translated': done, 'target_lang': target_lang, 'model': model}
