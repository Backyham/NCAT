"""Download stage: fetch source media from a URL via yt-dlp.

Rewritten from the old top-level ``download.py``. The yt-dlp option
construction is unchanged; what changed is that the function now returns a
resolved path (and reports through a callback) instead of printing its own
progress and writing wherever the process happened to be standing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import Report, noop_report

DEFAULT_OUTPUT_DIR = './downloads'


def default_proxy() -> str | None:
    """Proxy from the environment, if one is set.

    yt-dlp does read http_proxy/https_proxy, but only after its own
    connection setup has already happened, which is late enough to fail on
    hosts that need the proxy for reachability at all. Passing 'proxy'
    explicitly makes the request path deterministic.
    """
    for var in ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY'):
        val = os.environ.get(var)
        if val:
            return val
    return None


def download_opts(output_dir: str | Path,
                  cookies_from_browser: str | None = None,
                  audio_only: bool = False,
                  max_height: int | None = None,
                  proxy: str | None = None) -> dict:
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    opts: dict = {
        'outtmpl': str(out_dir / '%(title)s [%(id)s].%(ext)s'),
        'noplaylist': True,
        'quiet': False,
        'no_warnings': False,
        # Restrict to ASCII. CJK titles are common in this workflow and the
        # old 'windowsfilenames' flag mangled them into unreadable names.
        'restrictfilenames': False,
    }

    if audio_only:
        opts['format'] = (
            'bestaudio[acodec^=opus]/'
            'bestaudio[acodec^=aac]/'
            'bestaudio/best'
        )
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',
            'preferredquality': '192',
        }]
    else:
        # Cap the video height when asked. 'bestvideo' alone takes whatever
        # the site offers, which for a 1080p workflow silently becomes 2160p
        # and wastes bandwidth and disk on a result that is not wanted.
        vfilter = 'bestvideo' if max_height is None else f'bestvideo[height<={max_height}]'
        opts['format'] = (
            f'{vfilter}[ext=mp4]+bestaudio[ext=m4a]/'
            f'({vfilter}[ext=mp4]+bestaudio)/'
            f'({vfilter}+bestaudio)/'
            'best'
        )
        opts['merge_output_format'] = 'mp4'

    if cookies_from_browser:
        opts['cookiesfrombrowser'] = (cookies_from_browser,)

    effective_proxy = proxy or default_proxy()
    if effective_proxy:
        opts['proxy'] = effective_proxy

    return opts


def download(url: str,
             output_dir: str | Path = DEFAULT_OUTPUT_DIR,
             cookies_from_browser: str | None = None,
             audio_only: bool = False,
             max_height: int | None = None,
             proxy: str | None = None,
             report: Report = noop_report) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - env problem, not logic
        raise SystemExit(
            'yt-dlp is not installed in this interpreter. '
            'Install the app dependency group:  uv pip install yt-dlp'
        ) from exc

    report(f'downloading {url}')
    report(f'output dir {Path(output_dir).expanduser().resolve()}')
    if max_height:
        report(f'max height {max_height}p')
    if proxy or default_proxy():
        report(f'proxy {proxy or default_proxy()}')

    opts = download_opts(output_dir, cookies_from_browser, audio_only,
                         max_height=max_height, proxy=proxy)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = info.get('requested_downloads') or []
        if not requested:
            raise RuntimeError(
                'yt-dlp did not report a downloaded file path; '
                'check ffmpeg availability and output permissions'
            )
        filepath = (requested[0].get('filepath')
                    or requested[0].get('_filename'))
        if not filepath or not Path(filepath).is_file():
            raise RuntimeError(f'downloaded file not found on disk: {filepath}')

    path = Path(filepath).resolve()
    report(f'download complete: {path.name}')
    return path


def download_stage(job_id: int,
                   report: Report = noop_report,
                   paths=None,
                   url: str = '',
                   output_dir: str | Path = DEFAULT_OUTPUT_DIR,
                   audio_only: bool = False,
                   max_height: int | None = None,
                   proxy: str | None = None,
                   **_ignored) -> Path:
    """Entry point used by the web runner: returns the downloaded path."""
    return download(url, output_dir=output_dir, audio_only=audio_only,
                    max_height=max_height, proxy=proxy, report=report)
