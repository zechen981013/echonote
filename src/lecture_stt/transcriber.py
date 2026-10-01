"""转写引擎。想换模型时，照着 Transcriber 再写一个类即可，其他代码不用动。"""

import importlib.util
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .config import FASTER_WHISPER_MODEL, MLX_MODEL


@dataclass
class Segment:
    start: float  # 秒
    end: float
    text: str


@dataclass
class Transcript:
    language: str
    model: str
    segments: list[Segment]

    def to_dict(self) -> dict:
        return asdict(self)


class Transcriber(Protocol):
    name: str

    def download(self) -> str: ...

    def transcribe(
        self, media: Path, *, language: str | None, clip_seconds: float | None = None
    ) -> Transcript: ...


class MlxWhisperTranscriber:
    """Whisper 的 Apple 芯片优化版（MLX），只能在 M 系列 Mac 上用。"""

    def __init__(self, model: str = MLX_MODEL):
        self.name = model

    def download(self) -> str:
        from huggingface_hub import snapshot_download

        return snapshot_download(self.name)

    def transcribe(
        self, media: Path, *, language: str | None, clip_seconds: float | None = None
    ) -> Transcript:
        # 用到时才导入：scan / status 命令不需要加载这个很重的库
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(media),
            path_or_hf_repo=self.name,
            language=language,  # None 表示自动识别
            # 不把上一段的文字喂给下一段：长录音里能避免模型陷入重复同一句话
            condition_on_previous_text=False,
            clip_timestamps=[0, clip_seconds] if clip_seconds else "0",
            verbose=False,  # False = 只显示进度条
        )
        segments = [
            Segment(float(s["start"]), float(s["end"]), s["text"].strip())
            for s in result["segments"]
        ]
        return Transcript(result.get("language") or language or "", self.name, segments)


class FasterWhisperTranscriber:
    """Whisper 的 CTranslate2 版：Windows / Linux 上走 NVIDIA 显卡，没有显卡时退回 CPU。"""

    def __init__(self, model: str = FASTER_WHISPER_MODEL):
        self.name = model
        self._model = None

    def download(self) -> str:
        from faster_whisper import download_model

        return download_model(self.name)

    def _load(self):
        if self._model is None:  # 模型只加载一次，整批文件共用
            _add_nvidia_dll_dirs()
            from faster_whisper import WhisperModel

            # auto：有 NVIDIA 显卡就用显卡，并自动挑这块显卡支持的最快精度
            # （GTX 16 系不支持半精度，会自动落到 float32 / int8）
            self._model = WhisperModel(self.name, device="auto", compute_type="auto")
        return self._model

    def transcribe(
        self, media: Path, *, language: str | None, clip_seconds: float | None = None
    ) -> Transcript:
        from tqdm import tqdm

        pieces, info = self._load().transcribe(
            str(media),
            language=language,
            condition_on_previous_text=False,
            clip_timestamps=[0, clip_seconds] if clip_seconds else "0",
            vad_filter=not clip_seconds,  # 先切掉静音，减少模型在没人说话时乱编
        )
        segments = []
        # pieces 是生成器：边转写边吐结果，正好用来推进度条
        with tqdm(total=round(clip_seconds or info.duration), unit="s") as bar:
            for piece in pieces:
                segments.append(Segment(piece.start, piece.end, piece.text.strip()))
                bar.update(max(0, round(piece.end) - bar.n))
        return Transcript(info.language, self.name, segments)


def default_transcriber(model: str | None = None) -> Transcriber:
    """按当前电脑选引擎：M 系列 Mac 用 MLX，其他用 faster-whisper。"""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return MlxWhisperTranscriber(model or MLX_MODEL)
    return FasterWhisperTranscriber(model or FASTER_WHISPER_MODEL)


def _add_nvidia_dll_dirs() -> None:
    """Windows 上，pip 装的 NVIDIA 运行库（cuBLAS、cuDNN）不在系统搜索路径里，要手动加进去。"""
    if sys.platform != "win32":
        return
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    for base in spec.submodule_search_locations:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def probe_duration(media: Path) -> float | None:
    """读出时长（秒）。优先用 ffprobe；没装 ffmpeg 时用 PyAV（faster-whisper 自带）。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(media)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        import av

        with av.open(str(media)) as container:
            return container.duration / 1_000_000 if container.duration else None
    except Exception:
        return None
