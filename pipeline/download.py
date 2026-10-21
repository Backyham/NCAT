"""Download stage: fetch source media from a URL via yt-dlp.

Rewritten from the old top-level ``download.py``. The yt-dlp option
construction is unchanged; what changed is that the function now returns a
resolved path (and reports through a callback) instead of printing its own
progress and writing wherever the process happened to be standing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .paths import Report, noop_report

DEFAULT_OUTPUT_DIR = './downloads'


def download_opts(output_dir: str | Path,
                  cookies_from_browser: str | None = None,
                  audio_only: bool = False) -> dict:
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
        opts['format'] = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            '(bestvideo[ext=mp4]+bestaudio)/'
            '(bestvideo+bestaudio)/'
            'best'
        )
        opts['merge_output_format'] = 'mp4'

    if cookies_from_browser:
        opts['cookiesfrombrowser'] = (cookies_from_browser,)

    return opts


def download(url: str,
             output_dir: str | Path = DEFAULT_OUTPUT_DIR,
             cookies_from_browser: str | None = None,
             audio_only: bool = False,
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

    opts = download_opts(output_dir, cookies_from_browser, audio_only)
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
                   **_ignored) -> Path:
    """Entry point used by the web runner: returns the downloaded path."""
    return download(url, output_dir=output_dir, audio_only=audio_only,
                    report=report)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m pipeline.download',
        description='NCAT stage 1/7: download source media via yt-dlp.',
    )
    p.add_argument('url', help='Video URL (YouTube, Floatplane, ...)')
    p.add_argument('--out', default=DEFAULT_OUTPUT_DIR,
                   help='Output directory (default: ./downloads)')
    p.add_argument('--cookies-from-browser', default=None,
                   help='Browser to extract cookies from (chrome, firefox, edge)')
    p.add_argument('--audio-only', action='store_true',
                   help='Download bestaudio only and extract to m4a')
    args = p.parse_args(argv)

    try:
        path = download(args.url, args.out, args.cookies_from_browser,
                        audio_only=args.audio_only,
                        report=lambda m: print(m, flush=True))
    except Exception as exc:
        print(f'download failed: {exc}', file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
