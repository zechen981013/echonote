"""用 SQLite 记录每个视频的转写状态，实现“断点续跑、不重复转写”。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from .scanner import MediaFile, natural_key
from .transcriber import Transcript

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    course      TEXT NOT NULL,
    output_path TEXT NOT NULL,
    duration    REAL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / failed
    model       TEXT,
    error       TEXT,
    elapsed     REAL,
    finished_at TEXT
);
"""


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.segments_dir = data_dir / "segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "jobs.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    def reset_running(self) -> None:
        """上次异常退出时卡在 running 的任务，放回队列。"""
        with self.db:
            self.db.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")

    def register(self, media: MediaFile) -> None:
        """登记一个视频。已登记且文件没变的，保持原状态。"""
        stat = media.path.stat()
        row = self.db.execute("SELECT * FROM jobs WHERE path = ?", (str(media.path),)).fetchone()
        with self.db:
            if row is None:
                # 数据库里没有、但转写稿已经在了（比如换过电脑）：直接算完成
                status = "done" if media.output_path.exists() else "pending"
                self.db.execute(
                    "INSERT INTO jobs (path, size, mtime, course, output_path, status)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (str(media.path), stat.st_size, stat.st_mtime, media.course,
                     str(media.output_path), status),
                )
            elif (row["size"], row["mtime"]) != (stat.st_size, stat.st_mtime):
                # 文件被替换过：重新转写
                self.db.execute(
                    "UPDATE jobs SET size = ?, mtime = ?, course = ?, output_path = ?,"
                    " duration = NULL, status = 'pending', error = NULL WHERE id = ?",
                    (stat.st_size, stat.st_mtime, media.course, str(media.output_path), row["id"]),
                )
            elif row["status"] != "done":
                self.db.execute(
                    "UPDATE jobs SET course = ?, output_path = ? WHERE id = ?",
                    (media.course, str(media.output_path), row["id"]),
                )

    def jobs_under(self, root: Path, statuses: list[str] | None = None) -> list[sqlite3.Row]:
        rows = self.db.execute("SELECT * FROM jobs").fetchall()
        rows = [
            r for r in rows
            if (statuses is None or r["status"] in statuses)
            and (Path(r["path"]) == root or Path(r["path"]).is_relative_to(root))
            and Path(r["path"]).exists()
        ]
        return sorted(rows, key=lambda r: natural_key(Path(r["path"])))

    def all_jobs(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM jobs").fetchall()

    def set_duration(self, job_id: int, duration: float | None) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET duration = ? WHERE id = ?", (duration, job_id))

    def set_status(self, job_id: int, status: str, *, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE id = ?", (status, error, job_id)
            )

    def mark_done(self, job_id: int, model: str, elapsed: float) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status = 'done', error = NULL, model = ?, elapsed = ?,"
                " finished_at = ? WHERE id = ?",
                (model, elapsed, datetime.now().isoformat(timespec="seconds"), job_id),
            )

    def save_segments(self, job_id: int, transcript: Transcript) -> None:
        """原始分段数据单独存一份，以后想换排版不用重新转写。"""
        path = self.segments_dir / f"{job_id}.json"
        path.write_text(json.dumps(transcript.to_dict(), ensure_ascii=False), encoding="utf-8")

    def average_speed(self) -> float | None:
        """已完成任务的平均速度（几倍实时），用来估算剩余时间。"""
        row = self.db.execute(
            "SELECT SUM(duration) AS audio, SUM(elapsed) AS spent FROM jobs"
            " WHERE status = 'done' AND duration IS NOT NULL AND elapsed > 0"
        ).fetchone()
        if not row["audio"] or not row["spent"]:
            return None
        return row["audio"] / row["spent"]
