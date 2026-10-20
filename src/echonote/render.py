"""把转写结果排版成 Markdown。"""

import json
from datetime import date
from pathlib import Path

from .transcriber import Segment, Transcript


def format_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def group_paragraphs(
    segments: list[Segment], max_seconds: float = 75.0, pause: float = 2.0
) -> list[list[Segment]]:
    """把零碎的句子合成段落：遇到明显停顿，或一段够长且刚好说完一句时，另起一段。"""
    paragraphs: list[list[Segment]] = []
    current: list[Segment] = []
    for seg in segments:
        if not seg.text:
            continue
        if current:
            if seg.text == current[-1].text:  # 模型偶尔会连续重复同一句，丢掉
                continue
            gap = seg.start - current[-1].end
            length = current[-1].end - current[0].start
            sentence_ended = current[-1].text.endswith((".", "?", "!"))
            if gap >= pause or (length >= max_seconds and sentence_ended) or length >= max_seconds * 2:
                paragraphs.append(current)
                current = []
        current.append(seg)
    if current:
        paragraphs.append(current)
    return paragraphs


def render_markdown(
    transcript: Transcript, *, source: Path, course: str, duration: float | None,
    preview_minutes: float | None = None,
) -> str:
    # json.dumps 给字符串加引号并转义，文件名里有冒号、引号也不会破坏 YAML
    quote = lambda s: json.dumps(s, ensure_ascii=False)
    lines = [
        "---",
        f"source: {quote(source.name)}",
        f"course: {quote(course)}",
        f"duration: {quote(format_time(duration)) if duration else 'null'}",
        f"language: {transcript.language}",
        f"model: {transcript.model}",
        f"transcribed_at: {date.today().isoformat()}",
        "---",
        "",
        f"# {source.stem}",
        "",
    ]
    if preview_minutes:
        lines += [f"> 试跑：只转写了前 {preview_minutes:g} 分钟。", ""]
    for paragraph in group_paragraphs(transcript.segments):
        text = " ".join(seg.text for seg in paragraph)
        lines += [f"**[{format_time(paragraph[0].start)}]** {text}", ""]
    return "\n".join(lines)


def write_atomic(path: Path, content: str) -> None:
    """先写临时文件再改名：中途断电也不会留下半截的 md。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
