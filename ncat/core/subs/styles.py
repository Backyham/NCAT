"""ASS style-table editing for ``assets/template.ass``.

The template is the single source of truth for styles: export already reads it,
so editing it here makes the next ``style``/``render`` use the new values.
"""
from __future__ import annotations

import re
from pathlib import Path

from .ass import AssDocument, parse_ass, write_ass
from .model import default_ass_document

_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
TEMPLATE = "template.ass"

# Semantic key -> ASS style field.
FIELD_MAP = {
    "font": "Fontname",
    "size": "Fontsize",
    "primary": "PrimaryColour",
    "secondary": "SecondaryColour",
    "outline_color": "OutlineColour",
    "back_color": "BackColour",
    "outline": "Outline",
    "shadow": "Shadow",
    "alignment": "Alignment",
    "margin_l": "MarginL",
    "margin_r": "MarginR",
    "margin_v": "MarginV",
    "bold": "Bold",
    "italic": "Italic",
    "spacing": "Spacing",
}


def to_hex(value: str) -> str:
    """ASS ``&HAABBGGRR`` -> ``#RRGGBB`` (for ``<input type=color>``)."""
    text = str(value or "").strip().lstrip("&").lstrip("Hh").rstrip("&")
    if not text:
        return "#ffffff"
    text = text.zfill(8)[-6:]
    blue, green, red = text[0:2], text[2:4], text[4:6]
    return f"#{red}{green}{blue}".lower()


def to_ass_color(value: str) -> str:
    """``#RRGGBB`` -> ASS ``&H00BBGGRR``."""
    match = _COLOR.match(str(value or "").strip())
    if not match:
        return str(value or "")
    red, green, blue = (match.group(1)[0:2], match.group(1)[2:4],
                        match.group(1)[4:6])
    return f"&H00{blue}{green}{red}".upper()


def template_path(paths) -> Path:
    return paths.assets / TEMPLATE


def load_document(paths) -> tuple[AssDocument, Path | None]:
    path = template_path(paths)
    if path.is_file():
        return parse_ass(path), path
    return default_ass_document(), None


def payload(paths) -> dict:
    document, path = load_document(paths)
    return {
        "path": str(path) if path else None,
        "editable": True,
        "script_info": list(document.script_info),
        "style_format": list(document.style_format),
        "styles": [
            {"name": style.name, **style.fields,
             "primary_hex": to_hex(style.fields.get("PrimaryColour", "")),
             "outline_hex": to_hex(style.fields.get("OutlineColour", ""))}
            for style in document.styles
        ],
    }


def update_style(paths, name: str, values: dict) -> dict:
    document, _ = load_document(paths)
    style = next((item for item in document.styles if item.name == name), None)
    if style is None:
        style = default_ass_document().styles[0]
        style.name = name
        style.fields["Name"] = name
        document.styles.append(style)
    for key, value in values.items():
        field = FIELD_MAP.get(key)
        if field is None:
            continue
        if field in {"PrimaryColour", "SecondaryColour", "OutlineColour",
                     "BackColour"}:
            value = to_ass_color(str(value))
        style.fields[field] = str(value)
        if field == "Name":
            style.name = str(value)
    path = template_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_ass(path, document)
    return payload(paths)
