"""Prompt construction and response parsing for MT suggestions.

The prompt follows Index-Translate's canonical instTrans layout documented in
``docs/prompts_zh.md`` and ``inference/llm/translate.py``:

    header (source genre -> target language)
    【源文】
    ...
    【约束要求】
    1. 【硬性要求】...
    2. 【注意】...
    suffix

Segments are sent as a JSON object keyed by segment id. This preserves the
instTrans structured-data behaviour (keys unchanged, values translated) while
giving NCAT a stable id -> translation mapping.
"""
from __future__ import annotations

import json
import re

from .base import MTRequest, TargetSegment

# Language codes used by NCAT / the Index training prompt.
LANG_NAMES = {
    "en": "英语", "zh": "中文", "de": "德语", "fr": "法语",
    "es": "西班牙语", "ja": "日语", "ko": "韩语", "pt": "葡萄牙语",
    "ru": "俄语", "ar": "阿拉伯语", "it": "意大利语", "nl": "荷兰语",
    "pl": "波兰语", "ro": "罗马尼亚语", "sv": "瑞典语", "tr": "土耳其语",
    "hi": "印地语", "vi": "越南语", "th": "泰语", "id": "印尼语",
    "ms": "马来语", "fil": "菲律宾语", "ukr_cyrl": "乌克兰语",
    "fas_arab": "波斯语", "ces_latn": "捷克语", "ell_grek": "希腊语",
    "dan_latn": "丹麦语", "hun_latn": "匈牙利语", "fin_latn": "芬兰语",
    "nob_latn": "书面挪威语", "slk_latn": "斯洛伐克语",
    "bul_cyrl": "保加利亚语", "zh-hans": "中文", "zh-hant": "中文",
    "zh-cn": "中文", "zh-tw": "中文", "yue": "粤语",
}


def language_name(code: str | None) -> str:
    value = (code or "").strip()
    if not value:
        return ""
    key = value.lower()
    return (LANG_NAMES.get(key)
            or LANG_NAMES.get(key.replace("_", "-"))
            or value)


def _constraint_lines(request: MTRequest) -> list[str]:
    hard: list[str] = ["保留 JSON 结构，键名不变，只翻译 value。"]
    soft: list[str] = []

    terms = []
    forbidden = []
    for segment in request.segments:
        for hit in segment.term_hits:
            pair = f"{hit.source}→{hit.target}"
            if hit.forbidden:
                forbidden.append(pair)
            elif pair not in terms:
                terms.append(pair)
    if terms:
        hard.append(f"专名/术语对照: {'、'.join(terms)}")
    if forbidden:
        hard.append(f"不要使用以下译法: {'、'.join(dict.fromkeys(forbidden))}")

    tms = []
    for segment in request.segments:
        for match in segment.tm_matches:
            if match.match_type not in {"exact", "fuzzy"}:
                continue
            line = f"{match.source} => {match.target}"
            if match.score is not None:
                line += f" ({match.score:.2f})"
            if line not in tms:
                tms.append(line)
    if tms:
        soft.append("以下翻译记忆仅供参考，不强制采用: "
                    + "；".join(tms))

    if request.document_brief:
        soft.append(f"背景信息: {request.document_brief}")
    if request.style:
        soft.append(f"风格偏好: {request.style}")

    constraints = request.constraints or {}
    for item in constraints.get("hard", []) or []:
        if item:
            hard.append(str(item))
    for item in constraints.get("soft", []) or []:
        if item:
            soft.append(str(item))

    lines = [f"【硬性要求】{item}" for item in hard]
    lines += [f"【注意】{item}" for item in soft]
    return lines


def build_messages(request: MTRequest) -> list[dict[str, str]]:
    source_name = language_name(request.source_lang)
    target_name = language_name(request.target_lang) or request.target_lang
    genre = (request.genre or "字幕").strip()
    header_src = f"{source_name}{genre}" if source_name else genre
    header = (f"请将以下{header_src}翻译成{target_name}，"
              "并且严格遵循所有约束要求。")

    source_payload = {
        segment.id: segment.source for segment in request.segments
    }
    source_text = json.dumps(source_payload, ensure_ascii=False, indent=2)
    constraints = _constraint_lines(request)
    numbered = "\n".join(
        f"{index}. {line}" for index, line in enumerate(constraints, 1))
    suffix = ("请以相同的 JSON 格式输出翻译结果，key 保持不变，"
              "value 为对应译文。只输出 JSON，不要有任何额外说明。")
    content = (
        f"{header}\n\n"
        f"【源文】\n{source_text}\n\n"
        f"【约束要求】\n{numbered}\n\n"
        f"{suffix}"
    )
    return [{"role": "user", "content": content}]


def _strip_think(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip().removeprefix("<think>").strip()


def _parse_json(text: str, ids: list[str]) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    parsed: dict[str, str] = {}
    if isinstance(payload, dict):
        items = payload.get("translations") or payload.get("segments")
        if isinstance(items, dict):
            payload = items
            items = None
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("id", ids[index]
                                   if index < len(ids) else index))
                parsed[key] = str(item.get("target") or item.get("translation") or item.get("text") or "")
        else:
            for key, value in payload.items():
                if isinstance(value, str):
                    parsed[str(key)] = value
                elif isinstance(value, dict) and "target" in value:
                    parsed[str(key)] = str(value.get("target") or value.get("translation") or value.get("text") or "")
        if "id" in payload and "target" in payload:
            parsed[str(payload["id"])] = str(payload["target"])
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, dict):
                key = str(item.get("id", ids[index]
                                   if index < len(ids) else index))
                parsed[key] = str(item.get("target") or item.get("translation") or item.get("text") or "")
    return parsed


def _parse_text_fallback(text: str, ids: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip().strip("`\"")
        if key in ids:
            parsed[key] = value.strip().strip(",")
    return parsed


def parse_response(text: str, request: MTRequest) -> list[TargetSegment]:
    ids = [segment.id for segment in request.segments]
    cleaned = _strip_think((text or "").strip())
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = _parse_json(cleaned, ids)
    if not parsed:
        parsed = _parse_text_fallback(cleaned, ids)
    if not parsed and len(ids) == 1 and cleaned:
        parsed[ids[0]] = cleaned

    results: list[TargetSegment] = []
    for index, segment in enumerate(request.segments):
        target = parsed.get(segment.id, "")
        warnings = []
        if not target:
            warnings.append("missing_translation")
        if target and target.strip() == segment.source.strip():
            warnings.append("source_echo")
        results.append(TargetSegment(
            id=segment.id, target=target,
            confidence=1.0 if target and "missing_translation" not in warnings
            else 0.0,
            warnings=warnings,
        ))
    return results
