"""SQLite DAO(per-project)。

数据库文件位于 <project>/.yolo_studio/project.db。

文件系统的标注文件是真理源;DB 是镜像/索引,启动时由 manifest.rebuild_from_disk 重建。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    sha256 TEXT,
    split TEXT NOT NULL DEFAULT 'unassigned'
        CHECK(split IN ('train','val','test','unassigned')),
    is_done INTEGER NOT NULL DEFAULT 0,
    last_labeled_at TEXT,
    imported_at TEXT,
    labels_rotated INTEGER NOT NULL DEFAULT 0,
    is_ai INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_images_split ON images(split);
CREATE INDEX IF NOT EXISTS idx_images_done ON images(is_done);

CREATE TABLE IF NOT EXISTS annotations (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    xc REAL NOT NULL,
    yc REAL NOT NULL,
    w REAL NOT NULL,
    h REAL NOT NULL,
    PRIMARY KEY (image_id, idx)
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL,
    classes_json TEXT,
    metrics_json TEXT,
    parent_run TEXT,
    source TEXT,                       -- 'training' | 'imported' | 'external'
    created_at TEXT,
    sha256 TEXT
);

CREATE TABLE IF NOT EXISTS training_runs (
    id INTEGER PRIMARY KEY,
    model_id INTEGER REFERENCES models(id),
    started_at TEXT,
    finished_at TEXT,
    config_json TEXT,
    results_csv_path TEXT,
    final_metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class ImageRow:
    id: int
    path: str
    sha256: str | None
    split: str
    is_done: bool
    last_labeled_at: str | None
    imported_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ImageRow":
        return cls(
            id=row["id"],
            path=row["path"],
            sha256=row["sha256"],
            split=row["split"],
            is_done=bool(row["is_done"]),
            last_labeled_at=row["last_labeled_at"],
            imported_at=row["imported_at"],
        )


class ProjectDB:
    """单项目的 SQLite 句柄(线程安全)。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # 旧 DB 兼容:如果 images 表没有 labels_rotated 列,加上
            cur = conn.execute("PRAGMA table_info(images)")
            cols = {row[1] for row in cur.fetchall()}
            if "labels_rotated" not in cols:
                conn.execute(
                    "ALTER TABLE images ADD COLUMN labels_rotated INTEGER NOT NULL DEFAULT 0"
                )
            if "is_ai" not in cols:
                conn.execute(
                    "ALTER TABLE images ADD COLUMN is_ai INTEGER NOT NULL DEFAULT 0"
                )

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                yield cur
            finally:
                conn.close()

    def close(self) -> None:
        # 简化设计:连接每次操作新建/关闭,无需显式 close
        pass

    # ---- images ----
    def upsert_image(self, path: str, sha256: str | None = None) -> int:
        """插入或获取已存在的图像行,返回 id。"""
        with self.cursor() as cur:
            cur.execute("SELECT id FROM images WHERE path=?", (path,))
            row = cur.fetchone()
            if row:
                return int(row["id"])
            cur.execute(
                "INSERT INTO images (path, sha256, split, is_done, imported_at) "
                "VALUES (?, ?, 'unassigned', 0, ?)",
                (path, sha256, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            return int(cur.lastrowid)

    def set_split(self, image_id: int, split: str) -> None:
        with self.cursor() as cur:
            cur.execute("UPDATE images SET split=? WHERE id=?", (split, image_id))

    def set_done(self, image_id: int, is_done: bool) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE images SET is_done=?, last_labeled_at=? WHERE id=?",
                (1 if is_done else 0, time.strftime("%Y-%m-%dT%H:%M:%S"), image_id),
            )

    def set_is_ai(self, image_id: int, is_ai: bool) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE images SET is_ai=? WHERE id=?",
                (1 if is_ai else 0, image_id),
            )

    def get_ai_image_paths(self) -> set[str]:
        """返回所有标记为 AI 预标注的图像绝对路径集合。"""
        with self.cursor() as cur:
            cur.execute("SELECT path FROM images WHERE is_ai=1")
            return {row["path"] for row in cur.fetchall()}

    def get_labels_rotated(self, image_id: int) -> bool:
        """返回该图像的 .txt 是否已经在"已应用 EXIF 旋转"的坐标空间。

        False = .txt 还在原始(未旋转)坐标空间(需要 transform)
        True  = .txt 已在旋转后坐标空间(无需 transform)
        """
        with self.cursor() as cur:
            cur.execute("SELECT labels_rotated FROM images WHERE id=?", (image_id,))
            row = cur.fetchone()
            return bool(row["labels_rotated"]) if row else False

    def set_labels_rotated(self, image_id: int, rotated: bool) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE images SET labels_rotated=? WHERE id=?",
                (1 if rotated else 0, image_id),
            )

    def list_images(self, split: str | None = None) -> list[ImageRow]:
        sql = "SELECT * FROM images"
        params: tuple = ()
        if split:
            sql += " WHERE split=?"
            params = (split,)
        sql += " ORDER BY id"
        with self.cursor() as cur:
            cur.execute(sql, params)
            return [ImageRow.from_row(r) for r in cur.fetchall()]

    def get_image(self, image_id: int) -> Optional[ImageRow]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM images WHERE id=?", (image_id,))
            row = cur.fetchone()
            return ImageRow.from_row(row) if row else None

    def get_image_by_path(self, path: str) -> Optional[ImageRow]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM images WHERE path=?", (path,))
            row = cur.fetchone()
            return ImageRow.from_row(row) if row else None

    def count_images(self, split: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM images"
        params: tuple = ()
        if split:
            sql += " WHERE split=?"
            params = (split,)
        with self.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.fetchone()["c"])

    # ---- annotations ----
    def replace_annotations(self, image_id: int, boxes: list[tuple]) -> None:
        """boxes: list of (class_id, xc, yc, w, h)。"""
        with self.cursor() as cur:
            cur.execute("DELETE FROM annotations WHERE image_id=?", (image_id,))
            cur.executemany(
                "INSERT INTO annotations (image_id, idx, class_id, xc, yc, w, h) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (image_id, idx, cls, xc, yc, w, h)
                    for idx, (cls, xc, yc, w, h) in enumerate(boxes)
                ],
            )

    def get_annotations(self, image_id: int) -> list[tuple]:
        """返回 list of (class_id, xc, yc, w, h)。"""
        with self.cursor() as cur:
            cur.execute(
                "SELECT class_id, xc, yc, w, h FROM annotations "
                "WHERE image_id=? ORDER BY idx",
                (image_id,),
            )
            return [tuple(r) for r in cur.fetchall()]

    def count_annotations(self, image_id: int) -> int:
        with self.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM annotations WHERE image_id=?",
                (image_id,),
            )
            return int(cur.fetchone()["c"])

    # ---- settings_kv ----
    def kv_get(self, key: str) -> str | None:
        with self.cursor() as cur:
            cur.execute("SELECT value FROM settings_kv WHERE key=?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO settings_kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
