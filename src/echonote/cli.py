"""命令行入口：scan（预览）、run（转写）、status（进度）、download-model。"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from .config import DATA_DIR, DEFAULT_LANGUAGE, STT_SUFFIX
from .render import format_time, render_markdown, write_atomic
from .scanner import plan
from .store import Store
from .transcriber import default_transcriber, probe_duration


def _register(store: Store, root: Path) -> None:
    """扫描 root，把找到的视频登记进数据库，并补上时长。"""
    files = plan(root)
    for media in files:
        store.register(media)
    missing = [j for j in store.jobs_under(root) if j["duration"] is None]
    if missing:
        print(f"正在读取 {len(missing)} 个文件的时长…")
        for job in missing:
            store.set_duration(job["id"], probe_duration(Path(job["path"])))


@contextmanager
def _single_run_lock():
    """同一时间只允许一个转写进程：两个模型同时跑会把内存撑爆。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "run.lock", "w") as lock:
        try:
            if sys.platform == "win32":  # 文件锁在 Windows 和 Mac/Linux 上是两套写法
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("已有一个转写任务在运行，等它结束再试。")
        yield


@contextmanager
def _keep_awake():
    """转写期间不让电脑睡眠（挂机过夜用）。"""
    proc = None
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    elif sys.platform == "win32":
        import ctypes

        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED：告诉 Windows 现在有活在干，别睡
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        yield
    finally:
        if proc:
            proc.terminate()
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} 小时"


def _stt_dir(job) -> Path:
    """从输出路径里找出这门课的 _stt 文件夹。"""
    output = Path(job["output_path"])
    name = f"{job['course']}{STT_SUFFIX}"
    return next((p for p in output.parents if p.name == name), output.parent)


def cmd_scan(args: argparse.Namespace) -> None:
    root = Path(args.path).expanduser().resolve()
    store = Store()
    _register(store, root)
    jobs = store.jobs_under(root, include_audio=args.include_audio, exclude=tuple(args.exclude))
    if not jobs:
        print("没有找到视频文件（音频要加 --include-audio）。")
        return

    by_folder = defaultdict(list)  # 按每门课的 _stt 文件夹分组
    for job in jobs:
        by_folder[_stt_dir(job)].append(job)

    print(f"\n{'主题':<28}{'视频':>6}{'已完成':>8}{'时长':>12}")
    for folder, items in by_folder.items():
        done = sum(j["status"] == "done" for j in items)
        total = sum(j["duration"] or 0 for j in items)
        print(f"{items[0]['course'][:27]:<28}{len(items):>6}{done:>8}{_hours(total):>12}")
        if args.verbose:
            print(f"    -> {folder}")

    pending = [j for j in jobs if j["status"] != "done"]
    remaining = sum(j["duration"] or 0 for j in pending)
    print(f"\n共 {len(jobs)} 个文件，待转写 {len(pending)} 个，音频总长 {_hours(remaining)}。")
    speed = store.average_speed()
    if speed and remaining:
        print(f"按之前的平均速度（{speed:.1f} 倍实时）估算，还需约 {_hours(remaining / speed)}。")
    print("转写稿会放在每个主题文件夹下的 <主题>_stt/ 里。加 -v 可以看到具体路径。")


def _time_limit(audio_seconds: float) -> float:
    """一个文件最多允许跑多久：至少 20 分钟；长文件按“不能慢于 2 倍实时”算。"""
    return max(1200.0, audio_seconds / 2)


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def cmd_worker(args: argparse.Namespace) -> None:
    """只转写一个文件。由 run 以子进程方式调用，不直接给人用。"""
    store = Store()
    job = store.get(args.job_id)
    media = Path(job["path"])
    output = Path(job["output_path"])
    language = None if args.language == "auto" else args.language
    transcriber = default_transcriber(args.model)

    if args.preview_minutes:
        transcript = transcriber.transcribe(
            media, language=language, clip_seconds=args.preview_minutes * 60
        )
        output = output.with_suffix(".preview.md")
    else:
        transcript = transcriber.transcribe(media, language=language)
        store.save_segments(job["id"], transcript)
    write_atomic(output, render_markdown(
        transcript, source=media, course=job["course"],
        duration=job["duration"], preview_minutes=args.preview_minutes,
    ))


def cmd_run(args: argparse.Namespace) -> None:
    root = Path(args.path).expanduser().resolve()
    store = Store()
    # 被 kill 时也按 Ctrl-C 处理，这样正在跑的子进程会被一起收掉
    signal.signal(signal.SIGTERM, _raise_interrupt)

    with _single_run_lock():
        store.reset_running()
        _register(store, root)
        statuses = ["pending", "failed"] if args.retry_failed else ["pending"]
        if args.preview_minutes:
            statuses = None  # 试跑不看状态，也不改状态
        jobs = store.jobs_under(
            root, statuses, include_audio=args.include_audio, exclude=tuple(args.exclude)
        )
        if args.limit:
            jobs = jobs[: args.limit]
        if not jobs:
            print("没有待转写的文件。")
            return

        total = sum(j["duration"] or 0 for j in jobs)
        model_name = default_transcriber(args.model).name
        print(f"准备转写 {len(jobs)} 个文件，音频总长 {_hours(total)}。模型：{model_name}", flush=True)

        failures_in_a_row = 0
        with _keep_awake():
            for index, job in enumerate(jobs, 1):
                media = Path(job["path"])
                output = Path(job["output_path"])
                if args.preview_minutes:
                    output = output.with_suffix(".preview.md")
                audio = args.preview_minutes * 60 if args.preview_minutes else (job["duration"] or 0)
                print(f"\n[{index}/{len(jobs)}] {job['course']} / {media.name}", flush=True)

                # 每个文件单独开一个子进程来转写：
                # 1. 跑完进程退出，内存彻底还给系统，不会一个个文件越积越多；
                # 2. 卡住时可以从外面超时杀掉，换下一个，不会整批停在那里。
                command = [sys.executable, "-m", "echonote.cli", "_one", str(job["id"]),
                           "--language", args.language]
                if args.model:
                    command += ["--model", args.model]
                if args.preview_minutes:
                    command += ["--preview-minutes", str(args.preview_minutes)]
                limit = None if args.no_timeout else _time_limit(job["duration"] or 0)

                started = time.monotonic()
                error = None
                if not args.preview_minutes:
                    store.set_status(job["id"], "running")
                try:
                    subprocess.run(command, check=True, timeout=limit)
                except KeyboardInterrupt:
                    if not args.preview_minutes:
                        store.set_status(job["id"], "pending")
                    print("\n已中断。下次运行会从这个文件重新开始。")
                    return
                except subprocess.TimeoutExpired:
                    error = f"超过 {limit / 60:.0f} 分钟没跑完，已跳过"
                except subprocess.CalledProcessError as exc:
                    error = f"转写进程异常退出（退出码 {exc.returncode}）"
                if error:  # 一个文件坏了不该让整批停下
                    if not args.preview_minutes:
                        store.set_status(job["id"], "failed", error=error)
                    print(f"  失败：{error}", flush=True)
                    failures_in_a_row += 1
                    if failures_in_a_row >= 3:  # 连着失败多半是环境出了问题，别把整批都标成失败
                        print("\n连续 3 个文件失败，已停止。请查看上面的报错，修好后加 --retry-failed 重跑。")
                        return
                    continue
                failures_in_a_row = 0

                elapsed = time.monotonic() - started
                if not args.preview_minutes:
                    store.mark_done(job["id"], model_name, elapsed)
                speed = f"，{audio / elapsed:.1f} 倍实时" if audio and elapsed else ""
                print(f"  完成，用时 {format_time(elapsed)}{speed}\n  -> {output}", flush=True)


def cmd_status(args: argparse.Namespace) -> None:
    store = Store()
    jobs = store.all_jobs()
    if not jobs:
        print("还没有登记任何文件。先运行 echonote scan <文件夹>。")
        return
    for status, label in [("done", "已完成"), ("pending", "待转写"), ("failed", "失败"), ("running", "进行中")]:
        items = [j for j in jobs if j["status"] == status]
        if items:
            print(f"{label}：{len(items)} 个，{_hours(sum(j['duration'] or 0 for j in items))}")
    speed = store.average_speed()
    if speed:
        print(f"平均速度：{speed:.1f} 倍实时")
    for job in jobs:
        if job["status"] == "failed":
            print(f"  失败：{job['path']}\n        {job['error']}")


def cmd_download(args: argparse.Namespace) -> None:
    transcriber = default_transcriber(args.model)
    print(f"正在下载模型 {transcriber.name} …")
    print(f"已就绪：{transcriber.download()}")


def main() -> None:
    # Windows 终端默认编码可能显示不了个别字符，遇到时用 ? 代替而不是报错退出
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="echonote", description="递归扫描文件夹里的长音视频，转写成 Markdown。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描并预览：找到多少视频、总时长、预计用时")
    scan.add_argument("path", help="文件夹（会递归扫描）或单个视频文件")
    scan.add_argument("-v", "--verbose", action="store_true", help="按输出文件夹显示完整路径")
    scan.add_argument("--include-audio", action="store_true", help="也包含纯音频（mp3、m4a 等）")
    scan.add_argument("--exclude", action="append", default=[], metavar="TEXT", help="跳过路径中含此文字的文件，可重复")
    scan.set_defaults(func=cmd_scan)

    run = sub.add_parser("run", help="开始转写（已完成的自动跳过，可随时 Ctrl-C 中断）")
    run.add_argument("path", help="文件夹（会递归扫描）或单个视频文件")
    run.add_argument("--limit", type=int, help="这次最多处理几个文件")
    run.add_argument("--preview-minutes", type=float, metavar="N",
                     help="试跑：每个文件只转前 N 分钟，输出为 .preview.md")
    run.add_argument("--language", default=DEFAULT_LANGUAGE, help="语言代码，auto 为自动识别（默认 en）")
    run.add_argument("--model", help="不填则按当前电脑自动选")
    run.add_argument("--retry-failed", action="store_true", help="把之前失败的也重跑")
    run.add_argument("--include-audio", action="store_true", help="也转写纯音频（mp3、m4a 等），默认只转视频")
    run.add_argument("--exclude", action="append", default=[], metavar="TEXT", help="跳过路径中含此文字的文件，可重复")
    run.add_argument("--no-timeout", action="store_true", help="不限制单个文件的用时（只用 CPU 跑时需要）")
    run.set_defaults(func=cmd_run)

    worker = sub.add_parser("_one")  # 内部命令：run 用它在子进程里转写单个文件
    worker.add_argument("job_id", type=int)
    worker.add_argument("--preview-minutes", type=float)
    worker.add_argument("--language", default=DEFAULT_LANGUAGE)
    worker.add_argument("--model")
    worker.set_defaults(func=cmd_worker)

    status = sub.add_parser("status", help="查看总体进度")
    status.set_defaults(func=cmd_status)

    download = sub.add_parser("download-model", help="提前下载模型")
    download.add_argument("--model", help="不填则按当前电脑自动选")
    download.set_defaults(func=cmd_download)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
