"""递归扫描：找出文件夹里所有视频，并判断每个视频属于哪门课、转写稿该放哪。"""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import MEDIA_EXTS, SKIP_DIRS, STT_SUFFIX

# 课程代码：4 个字母 + 4 个数字，如 FINC3011。前后不能紧挨着字母/数字，
# 否则 MATHMATICS1311 会被误认成 TICS1311。
_COURSE_CODE = re.compile(r"(?<![A-Za-z])([A-Za-z]{4}\d{4})(?!\d)")

# “通用”文件夹名：它们只是课程里的分类，本身不代表一门课
_GENERIC_DIR = re.compile(
    r"""^(
        .*\b(recordings?|lectures?|lec|tutorials?|tut|videos?|sessions?)  # 以这些词结尾
      | (week|wk|chapter|topic|part|module|lesson)\s*[-_]?\s*\d+          # week 3 / chapter7
      | \d+                                                               # 纯数字，如日期
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# 输出路径里直接省掉的层级：FINANCE/recording/17.mp4 -> FINANCE_stt/17.md
_DROP_IN_OUTPUT = {"recording", "recordings", "video", "videos"}


@dataclass(frozen=True)
class MediaFile:
    path: Path          # 视频原文件
    course_dir: Path    # 课程文件夹
    course: str         # 课程名，用于给 _stt 文件夹命名
    output_path: Path   # 转写稿 .md 的位置


def natural_key(path: Path) -> list:
    """让 week 2 排在 week 10 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(path))]


def iter_media(root: Path) -> Iterator[Path]:
    """递归找出 root 下所有视频/音频文件。root 也可以直接是一个文件。"""
    if root.is_file():
        if root.suffix.lower() in MEDIA_EXTS:
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地修改 dirnames，os.walk 就不会进入被去掉的文件夹
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and not d.endswith(STT_SUFFIX)
        ]
        for name in filenames:
            if not name.startswith(".") and Path(name).suffix.lower() in MEDIA_EXTS:
                yield Path(dirpath) / name


def resolve_course(media: Path, root: Path) -> tuple[Path, str]:
    """判断一个视频属于哪门课。返回（课程文件夹, 课程名）。

    规则：
    1. 从视频所在文件夹往上到 root，名字里带课程代码的、最靠上的那一层就是课程；
    2. 都没有课程代码时，从下往上跳过“通用”文件夹名，第一个不通用的就是课程。
    """
    if root.is_file():
        # 单个文件：往上找到用户主目录为止
        home = Path.home()
        root = home if media.is_relative_to(home) else Path(media.anchor)

    ancestors = []  # 从视频所在文件夹开始，由下往上，到 root 为止（含 root）
    for folder in media.parents:
        ancestors.append(folder)
        if folder == root:
            break

    for folder in reversed(ancestors):  # 由上往下找课程代码
        match = _COURSE_CODE.search(folder.name)
        if match:
            return folder, match.group(1).upper()

    for folder in ancestors:  # 由下往上跳过通用名
        if not _GENERIC_DIR.match(folder.name):
            return folder, folder.name.strip()
    return ancestors[-1], ancestors[-1].name.strip()


def _output_path(media: Path, course_dir: Path, course: str, keep_all: bool) -> Path:
    relative = media.relative_to(course_dir).with_suffix(".md")
    parts = list(relative.parts)
    if not keep_all:
        parts = [p for p in parts[:-1] if p.lower() not in _DROP_IN_OUTPUT] + parts[-1:]
    return course_dir.joinpath(f"{course}{STT_SUFFIX}", *parts)


def plan(root: Path) -> list[MediaFile]:
    """扫描 root，给每个视频安排好课程和输出位置。"""
    files = []
    taken: set[Path] = set()
    for media in sorted(iter_media(root), key=natural_key):
        course_dir, course = resolve_course(media, root)
        output = _output_path(media, course_dir, course, keep_all=False)
        if output in taken:  # 省掉层级后撞名了，就保留完整路径
            output = _output_path(media, course_dir, course, keep_all=True)
        taken.add(output)
        files.append(MediaFile(media, course_dir, course, output))
    return files
