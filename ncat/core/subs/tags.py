"""ASS 文本的 override-tag 拆分与可见文本提取。

Segment/Cue 里存的是 ASS 原始文本（含 ``{\\bord1.6}`` 这类覆盖标签）。编辑器
不能把这些标签当可编辑字符，否则一次翻译就会毁掉排版。本模块是显示层与
保存层共用的唯一定义：

* ``spans()`` 把一行拆成 ``tag`` / ``text`` / ``break`` 三类片段；
* 前端按片段渲染：标签与换行是不可编辑的 chip，只有 ``text`` 可编辑；
* ``visible_text()`` 给 CPS、行长、搜索和列表用；
* ``is_drawing()`` 识别 ``{\\p1}`` 矢量绘图行——这类行不可翻译。
"""
from __future__ import annotations

import re

TAG = re.compile(r"\{[^}]*\}")
DRAWING = re.compile(r"\\p[1-9]\d*")
_BREAK = re.compile(r"(\\[Nnh])")


def _push_text(target: list[dict[str, str]], value: str) -> None:
    for piece in _BREAK.split(value):
        if not piece:
            continue
        kind = "break" if piece in {"\\N", "\\n", "\\h"} else "text"
        target.append({"kind": kind, "value": piece})


def spans(text: str) -> list[dict[str, str]]:
    """把 ASS 行拆成有序片段，标签原样保留。"""
    value = text or ""
    result: list[dict[str, str]] = []
    position = 0
    for match in TAG.finditer(value):
        if match.start() > position:
            _push_text(result, value[position:match.start()])
        result.append({"kind": "tag", "value": match.group(0)})
        position = match.end()
    if position < len(value):
        _push_text(result, value[position:])
    return result


def join_spans(items: list[dict[str, str]]) -> str:
    return "".join(str(item.get("value") or "") for item in items)


def visible_text(text: str, *, newline: str = "\n") -> str:
    """去掉覆盖标签，保留换行语义（``\\N`` → 换行，``\\h`` → 空格）。"""
    pieces: list[str] = []
    for item in spans(text):
        kind = item["kind"]
        if kind == "tag":
            continue
        if kind == "text":
            pieces.append(item["value"])
        elif item["value"] == "\\h":
            pieces.append(" ")
        else:
            pieces.append(newline)
    return "".join(pieces)


def is_drawing(text: str) -> bool:
    """``{\\p1}`` 起头的矢量绘图行不是可翻译文本。"""
    return bool(DRAWING.search(text or ""))


def tag_count(text: str) -> int:
    return sum(1 for item in spans(text) if item["kind"] == "tag")


def cue_kind(source_text: str, target_text: str) -> str:
    if is_drawing(target_text) or is_drawing(source_text):
        return "drawing"
    return "text"
