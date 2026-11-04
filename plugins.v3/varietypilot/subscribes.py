"""从 MoviePilot 现有订阅里发现综艺节目。

插件只需要「哪些剧在追」，不需要关心订阅其余字段，所以这里把订阅快照收敛成
最小组件：TMDB ID、名称、年份、季号集合。判定是不是综艺看 TMDB 类型，
结果落盘缓存，避免每次扫描都打 TMDB。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# TMDB 电视剧类型：真人秀、脱口秀。国内综艺基本落在这两类。
VARIETY_GENRE_IDS = frozenset({10764, 10767})
VARIETY_GENRE_WORDS = ('真人秀', '脱口秀', '综艺', 'reality', 'talk')

SUBTYPE_TV = '电视剧'
PAGE_LIMIT = 50


@dataclass
class SubscriptionItem:
    """一条订阅收敛后的最小信息。"""

    tmdb_id: int
    name: str
    year: str = ''
    seasons: list[int] = field(default_factory=list)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def list_tv_subscriptions(page_size: int = 200) -> list[Any]:
    """读取全部电视剧订阅快照。

    优先走 SDK 的只读查询；宿主版本或权限不满足时退回数据操作层，
    两处都拿不到就返回空列表——订阅同步失败不该影响插件其他功能。
    """
    try:
        from app.sdk.queries import list_subscriptions
    except Exception as err:  # noqa: BLE001
        logger.debug(f'综艺衍生内容管家：SDK 订阅查询不可用（{err}），改用数据层')
        return _list_by_oper()

    items: list[Any] = []
    page = 1
    while page <= PAGE_LIMIT:
        try:
            result = list_subscriptions(
                filters={'media_types': (SUBTYPE_TV,)},
                page={'page': page, 'count': page_size},
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(f'综艺衍生内容管家：读取订阅失败（第 {page} 页）：{err}')
            return items or _list_by_oper()
        batch = list(getattr(result, 'items', None) or [])
        items.extend(batch)
        total = _to_int(getattr(result, 'total', 0), 0)
        if not batch or len(batch) < page_size or (total and page * page_size >= total):
            break
        page += 1
    return items


def _list_by_oper() -> list[Any]:
    """兜底路径：直接读订阅表。"""
    try:
        from app.db.oper.subscribe import SubscribeOper

        rows = SubscribeOper().list()
    except Exception as err:  # noqa: BLE001
        logger.warning(f'综艺衍生内容管家：订阅读取全部失败：{err}')
        return []
    return [row for row in rows if str(_get(row, 'type', '') or '') == SUBTYPE_TV]


def collect(
    items: Iterable[Any],
    is_variety: Optional[Callable[[int], Optional[bool]]] = None,
    exclude_ids: Optional[Iterable[int]] = None,
    scope: str = 'variety',
) -> tuple[list[SubscriptionItem], list[str]]:
    """把订阅快照收敛成待跟踪剧集。

    :param items: 订阅快照或订阅表记录
    :param is_variety: 判定函数，返回 True/False/None（None 表示查不到类型）
    :param exclude_ids: 用户明确排除的 TMDB ID
    :param scope: `variety` 只要综艺，`all` 全部电视剧
    :return: (剧集列表, 跳过原因说明)
    """
    excluded = {_to_int(item, 0) for item in (exclude_ids or [])}
    grouped: dict[int, SubscriptionItem] = {}
    skipped: list[str] = []

    for item in items:
        source = str(_get(item, 'media_source', '') or '')
        if source and source != 'themoviedb':
            continue
        tmdb_id = _to_int(_get(item, 'media_id', 0), 0)
        if tmdb_id <= 0:
            continue
        if tmdb_id in excluded:
            continue

        name = str(_get(item, 'name', '') or '').strip()
        if not name:
            continue

        if scope != 'all':
            verdict = is_variety(tmdb_id) if is_variety else True
            if verdict is False:
                continue
            if verdict is None:
                # 类型查不到就跳过，但要说清楚原因，避免用户以为插件漏了
                skipped.append(f'{name}（TMDB {tmdb_id}）类型未知，已跳过')
                continue

        season = _get(item, 'season', None)
        season_no = _to_int(season, -1) if season is not None else -1

        exist = grouped.get(tmdb_id)
        if exist is None:
            grouped[tmdb_id] = SubscriptionItem(
                tmdb_id=tmdb_id,
                name=name,
                year=str(_get(item, 'year', '') or ''),
                seasons=[season_no] if season_no >= 0 else [],
            )
        elif season_no >= 0 and season_no not in exist.seasons:
            exist.seasons.append(season_no)

    shows = list(grouped.values())
    shows.sort(key=lambda row: row.name)
    return shows, skipped


class VarietyCache:
    """TMDB 类型判定结果缓存，按天过期。"""

    def __init__(self, path: Path, ttl_days: int = 30) -> None:
        self._path = Path(path)
        self._ttl = max(1, int(ttl_days)) * 86400
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as err:
            logger.debug(f'综艺衍生内容管家：类型缓存损坏已丢弃：{err}')
            return
        if isinstance(raw, dict):
            self._data = {
                str(key): value for key, value in raw.items() if isinstance(value, dict)
            }

    def get(self, tmdb_id: int) -> Optional[bool]:
        entry = self._data.get(str(tmdb_id))
        if not entry:
            return None
        if time.time() - float(entry.get('at') or 0) > self._ttl:
            return None
        value = entry.get('variety')
        return bool(value) if isinstance(value, bool) else None

    def set(self, tmdb_id: int, variety: bool) -> None:
        self._data[str(tmdb_id)] = {'variety': bool(variety), 'at': time.time()}

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding='utf-8'
            )
        except OSError as err:
            logger.debug(f'综艺衍生内容管家：类型缓存写入失败：{err}')


def is_variety_details(details: Any) -> bool:
    """按 TMDB 详情里的类型判断是否综艺。"""
    for genre in _get(details, 'genres', None) or []:
        if _to_int(_get(genre, 'id', 0), 0) in VARIETY_GENRE_IDS:
            return True
        word = str(_get(genre, 'name', '') or '').lower()
        if word and any(item in word for item in VARIETY_GENRE_WORDS):
            return True
    return False


class TmdbGenreChecker:
    """带缓存的 TMDB 综艺判定。"""

    def __init__(self, cache: VarietyCache) -> None:
        self._cache = cache
        self._client: Any = None
        self.fetched = 0

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        from app.modules.themoviedb.tmdbapi import TmdbApi

        self._client = TmdbApi()
        return self._client

    def save(self) -> None:
        """把本轮新查到的判定结果落盘。"""
        self._cache.save()

    def __call__(self, tmdb_id: int) -> Optional[bool]:
        cached = self._cache.get(tmdb_id)
        if cached is not None:
            return cached
        try:
            details = self._client_or_create().tv.details(tmdb_id)
        except Exception as err:  # noqa: BLE001
            logger.debug(f'综艺衍生内容管家：TMDB 类型查询失败（{tmdb_id}）：{err}')
            return None
        if not _get(details, 'id', None):
            return None
        verdict = is_variety_details(details)
        self._cache.set(tmdb_id, verdict)
        self.fetched += 1
        return verdict
