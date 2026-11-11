"""ASS parsing and serialisation that preserve styles and override tags."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})[.](\d{1,3})$")


def time_to_ms(value: str) -> int:
    match = _TIME.match(value.strip())
    if not match:
        raise ValueError(f"invalid ASS timestamp: {value!r}")
    hours, minutes, seconds, fraction = match.groups()
    centiseconds = int((fraction + "00")[:2])
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 \
        + centiseconds * 10


def ms_to_time(value: int) -> str:
    value = max(0, int(value))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


@dataclass
class AssStyle:
    name: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AssEvent:
    kind: str
    start_ms: int
    end_ms: int
    style: str
    text: str
    layer: str = "0"
    name: str = ""
    margin_l: str = "0"
    margin_r: str = "0"
    margin_v: str = "0"
    effect: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AssDocument:
    script_info: list[str] = field(default_factory=list)
    style_format: list[str] = field(default_factory=list)
    styles: list[AssStyle] = field(default_factory=list)
    event_format: list[str] = field(default_factory=list)
    events: list[AssEvent] = field(default_factory=list)
    extra: dict[str, list[str]] = field(default_factory=dict)

    def dialogues(self) -> list[AssEvent]:
        return [event for event in self.events if event.kind == "Dialogue"]


def _split_fields(payload: str, count: int) -> list[str]:
    if count <= 0:
        return []
    return payload.split(",", count - 1)


def _event_from_fields(kind: str, values: list[str],
                       names: list[str]) -> AssEvent:
    data = {name: value for name, value in zip(names, values)}
    return AssEvent(
        kind=kind,
        start_ms=time_to_ms(data.get("Start", "0:00:00.00")),
        end_ms=time_to_ms(data.get("End", "0:00:00.00")),
        style=data.get("Style", "Default"),
        text=data.get("Text", ""),
        layer=data.get("Layer", "0"),
        name=data.get("Name", ""),
        margin_l=data.get("MarginL", "0"),
        margin_r=data.get("MarginR", "0"),
        margin_v=data.get("MarginV", "0"),
        effect=data.get("Effect", ""),
        fields=data,
    )


def parse_ass(source: str | Path) -> AssDocument:
    text = (source.read_text(encoding="utf-8-sig", errors="replace")
            if isinstance(source, Path) else source)
    document = AssDocument()
    section = ""
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "Script Info":
            document.script_info.append(raw)
            continue
        if section == "V4+ Styles":
            if line.startswith("Format:"):
                document.style_format = [
                    part.strip() for part in line.split(":", 1)[1].split(",")
                ]
            elif line.startswith("Style:"):
                values = [part.strip() for part in _split_fields(
                    line.split(":", 1)[1].lstrip(","),
                    len(document.style_format))]
                data = dict(zip(document.style_format, values))
                document.styles.append(AssStyle(
                    name=data.get("Name", ""), fields=data))
            else:
                document.extra.setdefault(section, []).append(raw)
            continue
        if section == "Events":
            if line.startswith("Format:"):
                document.event_format = [
                    part.strip() for part in line.split(":", 1)[1].split(",")
                ]
            elif line.split(":", 1)[0] in {"Dialogue", "Comment"}:
                kind, payload = line.split(":", 1)
                values = _split_fields(payload.lstrip(","),
                                       len(document.event_format))
                document.events.append(
                    _event_from_fields(kind, values, document.event_format))
            else:
                document.extra.setdefault(section, []).append(raw)
            continue
        if section:
            document.extra.setdefault(section, []).append(raw)
    return document


def _event_line(event: AssEvent, format_names: list[str]) -> str:
    values = {
        "Layer": event.layer,
        "Start": ms_to_time(event.start_ms),
        "End": ms_to_time(event.end_ms),
        "Style": event.style,
        "Name": event.name,
        "MarginL": event.margin_l,
        "MarginR": event.margin_r,
        "MarginV": event.margin_v,
        "Effect": event.effect,
        "Text": event.text,
    }
    ordered = [values.get(name, event.fields.get(name, ""))
               for name in format_names]
    return f"{event.kind}: " + ",".join(ordered)


def render_ass(document: AssDocument) -> str:
    lines: list[str] = []
    lines.append("[Script Info]")
    lines.extend(document.script_info)
    lines.append("")
    lines.append("[V4+ Styles]")
    if document.style_format:
        lines.append("Format: " + ", ".join(document.style_format))
    for style in document.styles:
        values = [style.fields.get(name, "") for name in document.style_format]
        lines.append("Style: " + ",".join(values))
    for raw in document.extra.get("V4+ Styles", []):
        lines.append(raw)
    lines.append("")
    lines.append("[Events]")
    if document.event_format:
        lines.append("Format: " + ", ".join(document.event_format))
    for event in document.events:
        lines.append(_event_line(event, document.event_format))
    for raw in document.extra.get("Events", []):
        lines.append(raw)
    return "\ufeff" + "\r\n".join(lines) + "\r\n"


def write_ass(path: str | Path, document: AssDocument) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_ass(document).encode("utf-8"))
    return path
