"""任务存储：记录「哪条衍生内容要落到 S00 的第几集」。

存储用单个 JSON 文件，插件体积小、量级在千条以内，够用且便于排查。
所有读写走同一把锁，因为定时扫描线程与事件回调线程会并发访问。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

STATUS_QUEUED = 'queued'
STATUS_SUBMITTED = 'submitted'
STATUS_DONE = 'done'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'

VERIFY_UNKNOWN = 'unknown'
VERIFY_PRESENT = 'present'
VERIFY_MISSING = 'missing'


@dataclass
class Task:
    """一条衍生内容的落地任务。"""

    tmdb_id: int
    show_name: str
    sxx_episode: int
    sxx_name: str
    title: str
    src_season: Optional[int] = None
    src_episode: Optional[int] = None
    # 站点 SxxExx 里的编号。整理阶段拿到的是文件名，只有这组编号能可靠回连任务
    # （站点 E08 往往只是发布序号，与真实期号不是一回事）。
    src_site_season: Optional[int] = None
    src_site_episode: Optional[int] = None
    subtype: Optional[str] = None
    subtype_word: str = ''
    modifier: Optional[str] = None
    part: Optional[str] = None
    site: str = ''
    size: int = 0
    seeders: int = 0
    download_hash: str = ''
    status: str = STATUS_QUEUED
    verify: str = VERIFY_UNKNOWN
    error: str = ''
    match_score: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f'{self.tmdb_id}:{self.sxx_episode:04d}'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Task':
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def target_label(self) -> str:
        return f'S00E{self.sxx_episode:02d}'


@dataclass
class MissRecord:
    """扫到了衍生内容、但 TMDB 特别季里还没有登记对应集。

    这类记录不下载，等 TMDB 补齐后下一轮会自动命中。
    保留它是为了让「为什么这期没下」有据可查，而不是静默丢弃。
    """

    tmdb_id: int
    show_name: str
    title: str
    season: Optional[int] = None
    episode: Optional[int] = None
    subtype: Optional[str] = None
    reason: str = ''
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    count: int = 1

    @property
    def key(self) -> str:
        return f'{self.tmdb_id}:{self.title}'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'MissRecord':
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class TaskStore:
    """任务集合的持久化容器。"""

    def __init__(self, path: Path, max_records: int = 2000, max_misses: int = 300) -> None:
        self._path = Path(path)
        self._max = max(200, int(max_records))
        self._max_misses = max(50, int(max_misses))
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._misses: dict[str, MissRecord] = {}
        self._load()

    # ---------------------------------------------------------------- 持久化

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as err:
            logger.warning(f'综艺衍生内容管家：任务文件损坏，已重建（{err}）')
            return
        items = raw.get('tasks') if isinstance(raw, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    task = Task.from_dict(item)
                except (TypeError, ValueError):
                    continue
                if task.tmdb_id and task.sxx_episode:
                    self._tasks[task.key] = task

        misses = raw.get('misses') if isinstance(raw, dict) else None
        if isinstance(misses, list):
            for item in misses:
                if not isinstance(item, dict):
                    continue
                try:
                    miss = MissRecord.from_dict(item)
                except (TypeError, ValueError):
                    continue
                if miss.tmdb_id and miss.title:
                    self._misses[miss.key] = miss

    def _flush_locked(self) -> None:
        if len(self._tasks) > self._max:
            self._prune_locked()
        if len(self._misses) > self._max_misses:
            ordered = sorted(self._misses.values(), key=lambda item: item.last_seen, reverse=True)
            self._misses = {item.key: item for item in ordered[: self._max_misses]}
        payload = {
            'version': 2,
            'tasks': [task.to_dict() for task in self._tasks.values()],
            'misses': [miss.to_dict() for miss in self._misses.values()],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix('.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            temp.replace(self._path)
        except OSError as err:
            logger.warning(f'综艺衍生内容管家：任务写入失败（{err}）')

    def _prune_locked(self) -> None:
        """超限时优先淘汰已完成且较早的记录。"""
        ordered = sorted(
            self._tasks.values(),
            key=lambda task: (task.status in (STATUS_DONE, STATUS_SKIPPED), task.updated_at),
            reverse=True,
        )
        keep = {task.key for task in ordered[: self._max]}
        self._tasks = {k: v for k, v in self._tasks.items() if k in keep}

    # ---------------------------------------------------------------- 查询

    def get(self, tmdb_id: int, sxx_episode: int) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(f'{tmdb_id}:{sxx_episode:04d}')

    def has(self, tmdb_id: int, sxx_episode: int) -> bool:
        return self.get(tmdb_id, sxx_episode) is not None

    def all(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def recent(self, limit: int = 50) -> list[Task]:
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda task: task.updated_at, reverse=True)
            return ordered[:limit]

    def find_by_source(self, tmdb_id: int, season: Optional[int], episode: Optional[int]) -> Optional[Task]:
        """按源季/源集反查任务，供整理事件匹配。

        先按站点编号（SxxExx）找：MP 识别出来的季集号来自文件名，
        就是站点编号；找不到再退回真实期号。
        """
        if season is None or episode is None:
            return None
        with self._lock:
            for task in self._tasks.values():
                if (
                    task.tmdb_id == tmdb_id
                    and task.src_site_season == season
                    and task.src_site_episode == episode
                ):
                    return task
            for task in self._tasks.values():
                if (
                    task.tmdb_id == tmdb_id
                    and task.src_season == season
                    and task.src_episode == episode
                ):
                    return task
        return None

    def find_by_hash(self, download_hash: str) -> Optional[Task]:
        if not download_hash:
            return None
        with self._lock:
            for task in self._tasks.values():
                if task.download_hash and task.download_hash == download_hash:
                    return task
        return None

    def pending_transfer(self, tmdb_id: int) -> list[Task]:
        with self._lock:
            return [
                task for task in self._tasks.values()
                if task.tmdb_id == tmdb_id and task.status == STATUS_SUBMITTED
            ]

    def used_sxx_episodes(self, tmdb_id: int) -> set[int]:
        with self._lock:
            return {task.sxx_episode for task in self._tasks.values() if task.tmdb_id == tmdb_id}

    # ---------------------------------------------------------------- 写入

    def upsert(self, task: Task) -> Task:
        with self._lock:
            existing = self._tasks.get(task.key)
            if existing:
                task.created_at = existing.created_at
            task.updated_at = time.time()
            self._tasks[task.key] = task
            self._flush_locked()
            return task

    def update(self, task: Task, **changes: Any) -> Task:
        with self._lock:
            for name, value in changes.items():
                if hasattr(task, name):
                    setattr(task, name, value)
            task.updated_at = time.time()
            self._tasks[task.key] = task
            self._flush_locked()
            return task

    def remove(self, tmdb_id: int, sxx_episode: int) -> None:
        with self._lock:
            if self._tasks.pop(f'{tmdb_id}:{sxx_episode:04d}', None):
                self._flush_locked()

    # ------------------------------------------------------------ 未匹配记录

    def record_miss(self, miss: MissRecord) -> None:
        with self._lock:
            existing = self._misses.get(miss.key)
            if existing:
                existing.last_seen = time.time()
                existing.count += 1
                if miss.reason:
                    existing.reason = miss.reason
            else:
                self._misses[miss.key] = miss
            self._flush_locked()

    def misses(self, limit: int = 50) -> list[MissRecord]:
        with self._lock:
            ordered = sorted(self._misses.values(), key=lambda item: item.last_seen, reverse=True)
            return ordered[: max(1, limit)]

    def clear_misses(self) -> int:
        with self._lock:
            count = len(self._misses)
            self._misses.clear()
            self._flush_locked()
            return count

    def clear(self, statuses: Optional[Iterable[str]] = None) -> int:
        with self._lock:
            if statuses is None:
                count = len(self._tasks)
                self._tasks.clear()
            else:
                wanted = set(statuses)
                doomed = [k for k, v in self._tasks.items() if v.status in wanted]
                for key in doomed:
                    self._tasks.pop(key, None)
                count = len(doomed)
            self._flush_locked()
            return count

    def stats(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for task in self._tasks.values():
                result[task.status] = result.get(task.status, 0) + 1
            result['total'] = len(self._tasks)
            return result
