'''
Download Module of NCAT Toolkit.
Wraps yt-dlp to fetch source audio for ASR pipeline.

Output is bestaudio (m4a/webm/opus) - run transcode.py on it
to obtain the 16kHz mono PCM WAV consumed by the ASR modules.
'''
import os
import sys
from pathlib import Path

from exception_proc import show_exception_and_exit

sys.excepthook = show_exception_and_exit

DEFAULT_OUTPUT_DIR = './downloads'


def download_opts(url: str,
                  output_dir: str = DEFAULT_OUTPUT_DIR,
                  cookies_from_browser: str | None = None,
                  audio_only: bool = False) -> dict:
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        'outtmpl': str(out_dir / '%(title)s [%(id)s].%(ext)s'),
        'noplaylist': True,
        'quiet': False,
        'no_warnings': False,
        'restrictfilenames': True,
        'windowsfilenames': True,
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


def download_func(url: str,
                  output_dir: str = DEFAULT_OUTPUT_DIR,
                  cookies_from_browser: str | None = None,
                  audio_only: bool = False) -> str:
    try:
        import yt_dlp
    except ImportError:
        raise SystemError(
            'yt-dlp is not installed. Run: pip install yt-dlp'
        )

    opts = download_opts(url, output_dir, cookies_from_browser, audio_only)
    print(f"\nDownloading: {url}\nOutput dir: {output_dir}\n"
          f"Mode: {'audio-only' if audio_only else 'video+audio'}\n")

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = info.get('requested_downloads') or []
        if not downloaded:
            raise SystemError(
                'yt-dlp did not report a downloaded file path. '
                'Check ffmpeg availability and output permissions.'
            )
        filepath = downloaded[0].get('filepath') or downloaded[0].get('_filename')
        if not filepath or not os.path.isfile(filepath):
            raise SystemError(
                f'Downloaded file not found on disk: {filepath}'
            )

    print(f"\nDownload complete.\nPath: {filepath}\n")
    return filepath


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='NCAT Step 0: download source video via yt-dlp.'
    )
    parser.add_argument('url', help='Video URL (YouTube, Floatplane, etc.)')
    parser.add_argument('--out', default=DEFAULT_OUTPUT_DIR,
                        help='Output directory (default: ./downloads)')
    parser.add_argument('--cookies-from-browser', default=None,
                        help='Browser name for cookies extraction '
                             '(e.g. chrome, firefox, edge)')
    parser.add_argument('--audio-only', action='store_true',
                        help='Download bestaudio only, extract to m4a. '
                             'Default: video+audio combined.')
    args = parser.parse_args()

    try:
        download_func(args.url, args.out, args.cookies_from_browser,
                      audio_only=args.audio_only)
    except Exception as e:
        show_exception_and_exit(type(e), e, e.__traceback__)