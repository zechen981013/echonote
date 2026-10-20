import os
from pathlib import Path

# 认作“要转写”的文件：视频 + 纯音频（默认只转视频，音频要加 --include-audio）
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".flv", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

# 扫描时不进去的文件夹：全是依赖包或版本库，文件极多且不会有长音视频
SKIP_DIRS = {".venv", "venv", "node_modules", ".git", "__pycache__"}

# 转写稿文件夹的后缀，例如 FINC3011_stt
STT_SUFFIX = "_stt"

# 同一个模型（Whisper large-v3-turbo）在两种引擎下的名字
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"  # M 系列 Mac
FASTER_WHISPER_MODEL = "large-v3-turbo"             # Windows / Linux
DEFAULT_LANGUAGE = "en"

# 任务数据库和原始分段数据放在项目的 data/ 下，可用环境变量改到别处
DATA_DIR = Path(
    os.environ.get("LECTURE_STT_DATA")
    or Path(__file__).resolve().parents[2] / "data"
)
