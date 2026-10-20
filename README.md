# EchoNote

递归扫描文件夹里的长音视频，用本地 Whisper large-v3-turbo 模型转写成 Markdown。

| 电脑 | 引擎 | 说明 |
|---|---|---|
| M 系列 Mac | `mlx-whisper` | 已实测，M1 Pro 约 26 倍实时 |
| Windows + NVIDIA 显卡 | `faster-whisper` | 尚未实测；没有显卡时会退回 CPU，很慢 |

程序按当前电脑自动选引擎，`uv sync` 也只会装对应的那一套依赖。

## Windows 上第一次使用

1. 装好 NVIDIA 显卡驱动（能在命令行运行 `nvidia-smi` 即可，不需要单独装 CUDA）。
2. 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 git。
3. `git clone` 本仓库，进入目录后按下面的“用法”操作，路径换成 Windows 的写法，如 `D:\Media`。

任务进度记在各自电脑的 `data/` 里，两台电脑互不共享。

## 用法

```bash
uv sync                              # 安装依赖
uv run echonote download-model       # 下载模型（约 1.6G，只需一次）

uv run echonote scan ~/Videos        # 预览：找到多少视频、总时长
uv run echonote run ~/Videos         # 开始转写，Ctrl-C 可随时中断
uv run echonote status               # 查看进度
```

常用参数：

- `run --preview-minutes 3 --limit 1`：试跑，只转第一个文件的前 3 分钟，输出 `.preview.md`
- `run --limit 5`：这次只处理 5 个
- `run --language auto`：自动识别语言（默认按英语）
- `run --retry-failed`：重跑之前失败的

`run` 的路径可以是文件夹，也可以是单个视频。已转完的会自动跳过，中断后再次运行会接着跑。

## 输出位置

转写稿放在每个主题目录自己的文件夹里，文件夹名为 `<目录名>_stt`：

```
Media/FINC3011 International Financial Management/
├── Recording/week 1.mp4
└── FINC3011_stt/week 1.md
```

主题目录的判断规则（见 `scanner.py`）：文件夹名里带编号（4 字母 + 4 数字）的那一层作为主题；
没有编号时，跳过 `recording`、`week 3` 这类通用名，取第一个有意义的文件夹名。

## 代码结构

| 文件 | 作用 |
|---|---|
| `scanner.py` | 递归扫描、判断主题目录、安排输出路径 |
| `transcriber.py` | 转写引擎，换模型只改这里 |
| `render.py` | 把转写结果排版成 Markdown |
| `store.py` | SQLite 任务状态，实现断点续跑 |
| `cli.py` | 命令行入口 |

任务数据库和原始分段数据（JSON）在 `data/` 下。Mac 上不能放进 Docker 运行：MLX 需要直接使用 Mac 的 GPU。
