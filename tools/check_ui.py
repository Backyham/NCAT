"""Syntax-check the web UI's JavaScript.

A syntax error in the UI is silent in the worst way: the page still returns
HTTP 200 and looks correct, because the markup and styles render while
nothing runs. The symptom is an empty sidebar and dead buttons rather than
anything a browser reports prominently.

Both inline scripts and standalone modules are compiled, so the check still
works after the shell was split into ``css/`` and ``js/``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGETS = [ROOT / 'static' / 'index.html',
                   ROOT / 'static' / 'js']


def extract_inline(html: str) -> list[str]:
    return [m.group(1) for m in
            re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>',
                        html, re.DOTALL)
            if m.group(1).strip()]


def compile_with_quickjs(source: str, label: str) -> bool:
    """Compile without executing, so `document` need not exist."""
    import quickjs
    ctx = quickjs.Context()
    try:
        # Modules are wrapped: bare `import` is not valid in a function body,
        # so the import/export lines are stripped before the syntax check.
        body = re.sub(r'^\s*import\s[^;]+;', '', source, flags=re.M)
        body = re.sub(r'^\s*export\s+(?=(const|let|function|class|async))', '',
                      body, flags=re.M)
        ctx.eval(f'(function(){{\n{body}\n}})')
        return True
    except Exception as exc:  # noqa: BLE001 - report whatever the parser says
        print(f'  {label}: FAILED — {exc}')
        return False


def collect(targets: list[Path]) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    for target in targets:
        if target.is_file() and target.suffix == '.js':
            units.append((str(target), target.read_text('utf-8')))
        elif target.is_file() and target.suffix == '.html':
            for i, block in enumerate(extract_inline(target.read_text('utf-8')), 1):
                units.append((f'{target.name} inline #{i}', block))
        elif target.is_dir():
            for path in sorted(target.rglob('*.js')):
                units.append((str(path.relative_to(ROOT)), path.read_text('utf-8')))
    return units


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m tools.check_ui',
        description='Syntax-check the web UI JavaScript.',
    )
    p.add_argument('targets', nargs='*', type=Path,
                   help='HTML files or directories (default: the app shell)')
    args = p.parse_args(argv)
    targets = args.targets or DEFAULT_TARGETS

    try:
        import quickjs  # noqa: F401
    except ImportError:
        print('quickjs is not installed:  uv pip install quickjs',
              file=sys.stderr)
        return 2

    units = collect(targets)
    if not units:
        print('nothing to check', file=sys.stderr)
        return 2

    failed = [label for label, src in units if not compile_with_quickjs(src, label)]
    for label, src in units:
        if label not in failed:
            print(f'  ok   {label} ({len(src.splitlines())} lines)')
    if failed:
        print(f'\n{len(failed)} of {len(units)} failed')
        return 1
    print(f'\n{len(units)} file(s) parsed clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())