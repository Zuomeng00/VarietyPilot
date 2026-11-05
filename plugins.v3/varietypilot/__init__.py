"""综艺衍生内容管家 —— 把加更、彩蛋、纯享等衍生内容准确落到 TMDB 特别季。

设计要点：
1. 衍生内容由插件独立搜索与下载，不与正片订阅抢位，也不影响订阅完成度判定；
2. 落位依靠 TMDB 特别季的真实集号，不做「顺延到尾部」的猜测；
3. 在整理渲染前改写季集号，文件直接落到 Season 00，不搬动已入库文件。
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # MoviePilot V3 runtime
    from app.plugins import _PluginBase
    from app.sdk.events import Event, eventmanager
    from app.schemas.types import ChainEventType, EventType, MediaType

    HOST_AVAILABLE = True
except Exception:  # pragma: no cover - 脱离宿主时的兜底，便于独立测试
    HOST_AVAILABLE = False
    Event = Any  # type: ignore[misc,assignment]
    MediaType = Any  # type: ignore[misc,assignment]

    class _PluginBase:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._data: Dict[str, Any] = {}
            self._config: Dict[str, Any] = {}

        def get_data(self, key: str) -> Any:
            return self._data.get(key)

        def save_data(self, key: str, value: Any) -> None:
            self._data[key] = value

        def get_data_path(self) -> Path:
            return Path.cwd()

        def update_config(self, config: Dict[str, Any]) -> None:
            self._config = config

        def post_message(self, **kwargs: Any) -> None:
            return None

    class _EventManager:
        def register(self, *args: Any, **kwargs: Any):
            def decorator(func):
                return func

            return decorator

        def send_event(self, *args: Any, **kwargs: Any) -> None:
            return None

    eventmanager = _EventManager()  # type: ignore[assignment]

    class _FakeChainEventType:  # noqa: D401 - 仅用于脱离宿主时保持类定义可执行
        TransferRenameBuild = 'transfer.rename.build'
        TransferRename = 'transfer.rename'

    class _FakeEventType:
        TransferComplete = 'transfer.complete'

    class _FakeMediaType:
        TV = '电视剧'
        MOVIE = '电影'

    ChainEventType = _FakeChainEventType  # type: ignore[assignment]
    EventType = _FakeEventType  # type: ignore[assignment]
    MediaType = _FakeMediaType  # type: ignore[assignment]

from .pipeline import Candidate, Options, TrackedShow, scan_show
from .renaming import apply_to_rename_dict, rewrite_render_str
from .store import (
    STATUS_DONE,
    STATUS_QUEUED,
    STATUS_SKIPPED,
    STATUS_SUBMITTED,
    Task,
    TaskStore,
)
from .sxxindex import SpecialIndexStore
from .subscribes import TmdbGenreChecker, VarietyCache, collect, list_tv_subscriptions
from .titles import parse_title, subtype_label

logger = logging.getLogger(__name__)

PLUGIN_ID = 'VarietyPilot'

DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': False,
    'shows': '',
    'cron': '*/30 * * * *',
    'site_ids': [],
    'sync_subscribe': True,
    'sync_scope': 'variety',
    'sync_exclude': '',
    'sync_interval_minutes': 30,
    'min_seeders': 1,
    'min_size_gb': 0.05,
    'max_size_gb': 20.0,
    'prefer_resolution': '1080p',
    'dry_run': True,
    'notify': True,
    'refresh_library': True,
    'overwrite_existing': False,
    'guard_unmatched': False,
    'index_ttl_hours': 12,
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on', '是')
    if value is None:
        return default
    return bool(value)


_EXT_RE = re.compile(r'\.[A-Za-z0-9]{2,5}$')


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    """同时兼容字典快照与运行时对象两种取值方式。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_title(name: str) -> str:
    """归一化资源名，只用于判断「是不是同一个种子」。"""
    text = _EXT_RE.sub('', (name or '').strip())
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', text.lower())


class VarietyPilot(_PluginBase):
    # ---- 插件元信息
    plugin_name = '综艺衍生内容管家'
    plugin_desc = '把加更/彩蛋/纯享等衍生内容按 TMDB 特别季准确落位到 Season 00，自动跟踪 MP 订阅里的综艺，不占正片集号，极影视与 Emby 均可正常入库。'
    plugin_icon = 'mdi-television-classic'
    plugin_version = '2.1.4'
    plugin_author = '阿白'
    author_url = ''
    plugin_config_prefix = 'varietypilot_'
    plugin_order = 5
    auth_level = 1

    # ---- 运行时状态（不在 __init__ 做任何 IO）
    _enabled: bool = False
    _config: Dict[str, Any]
    _shows: List[TrackedShow]
    _manual_shows: List[TrackedShow]
    _auto_shows: List[TrackedShow]
    _sync_at: float = 0.0
    _sync_error: str = ''
    _sync_skipped: List[str]
    _store: Optional[TaskStore] = None
    _index_store: Optional[SpecialIndexStore] = None
    _data_path: Optional[Path] = None
    _last_scan_at: float = 0.0
    _last_report: str = ''
    _last_records: List[Dict[str, Any]] = []
    _scanning: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._options = Options()
        self._shows = []
        self._manual_shows = []
        self._auto_shows = []
        self._sync_skipped = []
        self._config = dict(DEFAULT_CONFIG)
        self._last_records = []
        self._media_cache: Dict[int, Any] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 配置

    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """生效配置。可能被反复调用，必须幂等。"""
        merged = dict(DEFAULT_CONFIG)
        if config:
            merged.update({k: v for k, v in config.items() if v is not None})
        self._config = merged

        self._enabled = _to_bool(merged.get('enabled'))
        self._options = Options(
            min_seeders=_to_int(merged.get('min_seeders'), 1),
            min_size_gb=_to_float(merged.get('min_size_gb'), 0.05),
            max_size_gb=_to_float(merged.get('max_size_gb'), 20.0),
            prefer_resolution=str(merged.get('prefer_resolution') or '1080p'),
            overwrite_existing=_to_bool(merged.get('overwrite_existing')),
        )
        self._manual_shows = self._parse_shows(
            str(merged.get('shows') or ''), merged.get('site_ids')
        )
        self._auto_shows = []
        self._sync_skipped = []

        try:
            self._data_path = self.get_data_path()
        except Exception as err:  # noqa: BLE001
            logger.error(f'综艺衍生内容管家：获取插件数据目录失败：{err}')
            self._data_path = None

        if self._data_path:
            self._store = TaskStore(self._data_path / 'tasks.json')
            self._index_store = SpecialIndexStore(
                cache_dir=self._data_path / 'sxx',
                ttl_seconds=_to_int(merged.get('index_ttl_hours'), 12) * 3600,
            )
            # 订阅同步结果落盘，重启后先按缓存生效，不必等到下一轮扫描
            self._load_auto_shows()

        self._rebuild_shows()
        logger.info(
            f'综艺衍生内容管家：配置已生效，状态={"启用" if self._enabled else "停用"}，'
            f'手动 {len(self._manual_shows)} 部 / 订阅 {len(self._auto_shows)} 部，'
            f'预演={"开" if _to_bool(merged.get("dry_run"), True) else "关"}'
        )
        if self._enabled and _to_bool(merged.get('sync_subscribe'), True):
            self._schedule_first_sync()

    def get_state(self) -> bool:
        return bool(self._enabled)

    @staticmethod
    def _parse_site_ids(raw: Any) -> List[int]:
        """站点配置既可能是多选返回的数组，也可能是旧版逗号字符串。"""
        if isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = str(raw or '').replace('，', ',').split(',')
        sites: List[int] = []
        for item in values:
            value = _to_int(str(item).strip(), 0)
            if value > 0 and value not in sites:
                sites.append(value)
        return sites

    @classmethod
    def _parse_shows(cls, text: str, site_raw: Any) -> List[TrackedShow]:
        """解析剧列表，每行 `剧名|TMDBID|年份`, 每行 `剧名|TMDBID|别名1,别名2` 也可用。"""
        shows: List[TrackedShow] = []
        sites = cls._parse_site_ids(site_raw)
        for raw in (text or '').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            parts = [item.strip() for item in line.replace('｜', '|').split('|')]
            if len(parts) < 2:
                logger.warning(f'综艺衍生内容管家：剧列表行格式不正确，已忽略：{line}')
                continue
            name = parts[0]
            tmdb_id = _to_int(parts[1], 0)
            if not name or tmdb_id <= 0:
                logger.warning(f'综艺衍生内容管家：剧列表缺少剧名或 TMDB ID，已忽略：{line}')
                continue
            year = ''
            aliases: List[str] = []
            if len(parts) >= 3 and parts[2]:
                extra = parts[2]
                if extra.isdigit():
                    year = extra
                else:
                    aliases = [item.strip() for item in extra.replace('，', ',').split(',') if item.strip()]
            if len(parts) >= 4 and parts[3]:
                aliases = [item.strip() for item in parts[3].replace('，', ',').split(',') if item.strip()]
            shows.append(TrackedShow(name=name, tmdb_id=tmdb_id, year=year, aliases=aliases, sites=sites))
        return shows

    def _configured_site_ids(self) -> List[int]:
        """当前配置里填写的站点 ID，去重保序。"""
        return self._parse_site_ids(self._config.get('site_ids'))

    # -------------------------------------------------------------- 订阅同步

    def _auto_cache_file(self) -> Optional[Path]:
        if not self._data_path:
            return None
        return self._data_path / 'auto_shows.json'

    def _load_auto_shows(self) -> None:
        """恢复上次同步出的订阅剧集，重启后立刻可用，不必等网络。"""
        if not _to_bool(self._config.get('sync_subscribe'), True):
            return
        path = self._auto_cache_file()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as err:
            logger.debug(f'综艺衍生内容管家：订阅缓存损坏已丢弃：{err}')
            return
        if not isinstance(raw, dict):
            return
        sites = self._configured_site_ids()
        shows: List[TrackedShow] = []
        for item in raw.get('shows') or []:
            if not isinstance(item, dict):
                continue
            tmdb_id = _to_int(item.get('tmdb_id'), 0)
            name = str(item.get('name') or '').strip()
            if tmdb_id <= 0 or not name:
                continue
            shows.append(
                TrackedShow(
                    name=name,
                    tmdb_id=tmdb_id,
                    year=str(item.get('year') or ''),
                    sites=sites,
                )
            )
        self._auto_shows = shows
        self._sync_at = _to_float(raw.get('at'), 0.0)
        self._sync_skipped = [str(item) for item in (raw.get('skipped') or []) if item]

    def _save_auto_shows(self) -> None:
        path = self._auto_cache_file()
        if path is None:
            return
        payload = {
            'at': self._sync_at,
            'skipped': self._sync_skipped,
            'shows': [
                {'tmdb_id': show.tmdb_id, 'name': show.name, 'year': show.year}
                for show in self._auto_shows
            ],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError as err:
            logger.debug(f'综艺衍生内容管家：订阅缓存写入失败：{err}')

    def _rebuild_shows(self) -> None:
        """手动配置优先，同一部剧不会被订阅同步重复加入。"""
        merged: List[TrackedShow] = []
        seen: set[int] = set()
        for show in [*self._manual_shows, *self._auto_shows]:
            if show.tmdb_id in seen:
                continue
            seen.add(show.tmdb_id)
            merged.append(show)
        with self._lock:
            self._shows = merged

    def refresh_subscriptions(self) -> Tuple[int, str]:
        """从 MP 现有订阅里同步综艺，返回 (新增/更新数量, 说明)。

        只在扫描线程里调用，页面渲染不触发网络。
        """
        if not _to_bool(self._config.get('sync_subscribe'), True):
            if self._auto_shows:
                self._auto_shows = []
                self._sync_at = 0.0
                self._sync_skipped = []
                self._save_auto_shows()
                self._rebuild_shows()
            return 0, '未开启订阅同步'

        raw_exclude = str(self._config.get('sync_exclude') or '')
        exclude_ids = self._parse_site_ids(raw_exclude)
        scope = str(self._config.get('sync_scope') or 'variety')

        items = list_tv_subscriptions()
        if not items:
            return 0, '没有读取到电视剧订阅（或当前账号无订阅权限）'

        cache_file = (self._data_path / 'genre_cache.json') if self._data_path else None
        checker = TmdbGenreChecker(VarietyCache(cache_file)) if cache_file else None
        found, skipped = collect(
            items,
            is_variety=checker if checker else None,
            exclude_ids=exclude_ids,
            scope=scope,
        )
        if checker:
            checker.save()

        sites = self._configured_site_ids()
        self._auto_shows = [
            TrackedShow(name=row.name, tmdb_id=row.tmdb_id, year=row.year, sites=sites)
            for row in found
        ]
        self._sync_at = time.time()
        self._sync_skipped = skipped
        self._sync_error = ''
        self._save_auto_shows()
        self._rebuild_shows()
        note = f'订阅同步完成：{len(self._auto_shows)} 部'
        if skipped:
            note += f'，{len(skipped)} 部类型未知已跳过'
        return len(self._auto_shows), note

    def _sync_interval_minutes(self) -> int:
        """订阅同步间隔（分钟）。0 表示每轮扫描都同步。"""
        value = _to_int(self._config.get('sync_interval_minutes'), 30)
        return max(0, min(value, 24 * 60))

    def _sync_due(self) -> bool:
        """是否到了该同步订阅的时间。

        订阅列表变化很慢，没必要跟着扫描周期反复拉取：MP 端每同步一次都要
        挨个查 TMDB 类型，站点多、订阅多时开销不小，还会刷屏。
        """
        interval = self._sync_interval_minutes()
        if interval <= 0 or self._sync_at <= 0:
            return True
        return (time.time() - self._sync_at) >= interval * 60

    @staticmethod
    def site_catalog() -> List[Dict[str, Any]]:
        """列出宿主已接入的站点，供填写「限定站点 ID」时对照。

        读的是宿主站点库，不改任何数据；取不到时返回空列表，
        页面只会少一块内容，不影响其他功能。
        """
        try:
            from app.sdk.network import SitesHelper

            indexers = SitesHelper().get_indexers() or []
        except Exception as err:  # noqa: BLE001
            logger.debug(f'综艺衍生内容管家：读取站点列表失败：{err}')
            return []
        catalog: List[Dict[str, Any]] = []
        for item in indexers:
            if not isinstance(item, dict):
                continue
            site_id = _to_int(item.get('id'), 0)
            if site_id <= 0:
                continue
            catalog.append(
                {
                    'id': site_id,
                    'name': str(item.get('name') or ''),
                    'domain': str(item.get('domain') or ''),
                    'public': bool(item.get('public')),
                }
            )
        catalog.sort(key=lambda row: (row['public'], row['id']))
        return catalog

    def _site_warnings(self) -> List[str]:
        """配置了但站点库里找不到的 ID，MP 会静默按「全部站点」处理，必须提示出来。"""
        wanted = self._configured_site_ids()
        if not wanted:
            return []
        catalog = {row['id']: row for row in self.site_catalog()}
        if not catalog:
            return []
        missing = [str(site_id) for site_id in wanted if site_id not in catalog]
        if not missing:
            return []
        return [
            f'站点 ID {", ".join(missing)} 在当前站点库里不存在或未启用，'
            f'MP 会按「全部站点」搜索。请核对后修正，或清空此项。'
        ]

    # ------------------------------------------------------------------- 服务

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        cron = str(self._config.get('cron') or '*/30 * * * *')
        trigger: Any = cron
        try:
            from apscheduler.triggers.cron import CronTrigger

            trigger = CronTrigger.from_crontab(cron)
        except Exception as err:  # noqa: BLE001
            logger.warning(f'综艺衍生内容管家：Cron 解析失败，改用原始表达式 {cron}：{err}')
        return [
            {
                'id': 'VarietyPilotScan',
                'name': '综艺衍生内容扫描',
                'trigger': trigger,
                'func': self.scan_task,
                'kwargs': {},
            }
        ]

    def _schedule_first_sync(self) -> None:
        """启动后补一次订阅同步，避免要等满一个扫描周期才看到订阅的剧。"""
        try:
            from app.sdk.scheduler import add_plugin_once_job

            add_plugin_once_job(
                plugin_id=PLUGIN_ID,
                job_id='first_sync',
                func=self._first_sync,
                name='综艺衍生内容-订阅同步',
                delay_seconds=90,
            )
        except Exception as err:  # noqa: BLE001
            logger.debug(f'综艺衍生内容管家：初始订阅同步注册失败：{err}')

    def _first_sync(self) -> None:
        if not self._enabled:
            return
        if not self._sync_due():
            return
        try:
            count, note = self.refresh_subscriptions()
            logger.info(f'综艺衍生内容管家：{note}')
        except Exception as err:  # noqa: BLE001
            logger.warning(f'综艺衍生内容管家：初始订阅同步失败：{err}')

    def stop_service(self) -> None:
        """释放扫描状态。插件不建自建线程，无需额外回收。"""
        self._scanning = False

    # ------------------------------------------------------------------- 扫描

    def scan_task(self) -> None:
        """定时任务入口。"""
        if not self._enabled or self._scanning:
            return
        self.run_scan(dry_run=_to_bool(self._config.get('dry_run'), True))

    def run_scan(
        self,
        dry_run: Optional[bool] = None,
        only_show: str = '',
        force_sync: bool = False,
    ) -> Tuple[int, str]:
        """执行一轮扫描，返回 (提交数量, 文本汇总)。"""
        if self._store is None or self._index_store is None:
            return 0, '插件尚未初始化，请先保存配置'

        sync_note = ''
        if _to_bool(self._config.get('sync_subscribe'), True):
            if force_sync or self._sync_due():
                try:
                    _, sync_note = self.refresh_subscriptions()
                except Exception as err:  # noqa: BLE001
                    sync_note = f'订阅同步失败：{err}'
                    logger.warning(f'综艺衍生内容管家：{sync_note}')
            else:
                interval = self._sync_interval_minutes()
                left = max(0, interval * 60 - (time.time() - self._sync_at))
                logger.debug(
                    f'综艺衍生内容管家：订阅同步未到期（间隔 {interval} 分钟，'
                    f'还需 {left / 60:.0f} 分钟），本轮跳过'
                )

        if not self._shows:
            return 0, sync_note or '未配置跟踪剧集，请在插件设置里填写「剧名|TMDB ID」'

        dry = _to_bool(self._config.get('dry_run'), True) if dry_run is None else dry_run
        targets = [show for show in self._shows if not only_show or show.name == only_show]
        if not targets:
            return 0, f'未找到剧集：{only_show}'

        with self._lock:
            self._scanning = True
        lines: List[str] = []
        lines.extend(self._site_warnings())
        if sync_note:
            lines.append(sync_note)
        submitted = 0
        collected: List[Dict[str, Any]] = []
        try:
            for show in targets:
                try:
                    index = self._index_store.load(show.tmdb_id, show.name)
                except Exception as err:  # noqa: BLE001
                    lines.append(f'{show.name}：特别季索引获取失败（{err}）')
                    continue
                report = scan_show(
                    show=show,
                    index=index,
                    store=self._store,
                    options=self._options,
                    search_fn=self._search,
                    download_fn=None if dry else self._download,
                    dry_run=dry,
                )
                submitted += report.submitted
                collected.extend(report.records)
                lines.append(report.line())
                # 明细已按「失败 → 已提交 → 跳过」排好，失败原因排在最前
                for detail in report.details[:20]:
                    lines.append(f'  · {detail}')
            if len(collected) > 20:
                lines.append(f'每条资源的落位与原因共 {len(collected)} 条，见插件页面「最近扫描明细」')
            self._last_scan_at = time.time()
            self._last_records = collected[-400:]
            self._last_report = '\n'.join(lines)
            summary = '\n'.join(lines)
            if _to_bool(self._config.get('notify'), True) and (submitted or not dry):
                title = '【综艺衍生内容】预演结果' if dry else '【综艺衍生内容】已提交下载'
                self.post_message(title=title, text=summary[:3000])
            return submitted, summary
        finally:
            with self._lock:
                self._scanning = False

    # --------------------------------------------------------------- 外部调用

    def _search(self, name: str, sites: List[int]) -> List[Any]:
        from app.chain.search import SearchChain

        chain = SearchChain()
        try:
            result = chain.search_by_title(title=name, sites=sites or None, page=0)
        except TypeError:
            result = chain.search_by_title(name, 0, sites or None)
        return list(result or [])

    def _media_info(self, tmdb_id: int) -> Any:
        """按 TMDB ID 取媒体信息，供下载链使用。

        搜索返回的上下文只有种子与元数据两部分，`media_info` 默认是空的，
        而下载链要求它必须是 MediaInfo 对象，缺了会直接拒绝整次提交。
        这里按剧缓存一份，每条资源提交时再深拷贝，避免季集字段被上一条污染。
        """
        if not tmdb_id:
            return None
        cache: Dict[int, Any] = self._media_cache
        if tmdb_id in cache:
            return cache[tmdb_id]

        media = None
        try:
            from app.chain.media import MediaChain
            from app.schemas.types import MediaSource

            chain = MediaChain()
            try:
                media = chain.recognize_media(
                    mtype=MediaType.TV,
                    media_source=MediaSource.TMDB,
                    media_id=str(tmdb_id),
                )
            except TypeError:
                # 兼容早期签名：直接按 TMDB ID 识别
                media = chain.recognize_media(tmdbid=tmdb_id, mtype=MediaType.TV)
        except Exception as err:  # noqa: BLE001
            logger.error(f'综艺衍生内容管家：获取 TMDB {tmdb_id} 媒体信息失败：{err}')
        if media is None:
            logger.warning(f'综艺衍生内容管家：TMDB {tmdb_id} 未识别到媒体信息')
        cache[tmdb_id] = media
        return media

    @staticmethod
    def _align_meta(context: Any, candidate: Candidate) -> None:
        """把元数据的季集号对齐到特别集。

        MP 的下载历史、下载前存在性校验、整理时的默认识别都以 meta_info 为准。
        若继续用站点的 S04E08，这条加更会被登记成「第4季第8集」，
        既可能让正片订阅误判为已下，也会在整理阶段压到正片集号上。
        这里只改季集四个字段，标题等其余识别结果保持原样。
        """
        meta = getattr(context, 'meta_info', None)
        if meta is None:
            return
        try:
            meta.begin_season = 0
            meta.end_season = None
            meta.begin_episode = int(candidate.match.episode)
            meta.end_episode = None
            meta.total_episode = None
        except Exception as err:  # noqa: BLE001
            logger.debug(f'综艺衍生内容管家：季集对齐失败，沿用原始元数据：{err}')

    def _download(self, candidate: Candidate) -> Tuple[bool, str]:
        from app.chain.download import DownloadChain

        context = candidate.context
        if getattr(context, 'torrent_info', None) is None or getattr(context, 'meta_info', None) is None:
            return False, '下载上下文缺少种子或元数据，无法提交'

        # 搜索结果默认不带 media_info，必须由插件补齐下载链才肯接收
        if getattr(context, 'media_info', None) is None:
            media = self._media_info(candidate.tmdb_id)
            if media is None:
                return False, f'未能获取 TMDB {candidate.tmdb_id} 的媒体信息，无法提交'
            context.media_info = copy.deepcopy(media)

        # 提交前把季集对齐到特别季：下载历史、下载前存在性判断、
        # 以及后续整理渲染都会以 S00 为准，不再使用站点的发布序号。
        self._align_meta(context, candidate)

        chain = DownloadChain()
        try:
            value = chain.download_single(
                context=candidate.context,
                source='综艺衍生内容',
                return_detail=True,
            )
        except TypeError:
            value = chain.download_single(candidate.context)
        except Exception as err:  # noqa: BLE001
            logger.error(f'综艺衍生内容管家：提交下载异常《{candidate.title}》：{err}')
            return False, f'调用下载接口异常：{err}'
        if isinstance(value, tuple):
            download_hash = value[0] if value else None
            message = value[1] if len(value) > 1 else ''
            if download_hash:
                return True, str(download_hash)
            return False, str(
                message
                or '下载器未接受该资源且未返回原因（常见于站点种子获取失败、'
                   '未配置下载目录，或该种子已被 MP 记入下载失败黑名单）'
            )
        if value:
            return True, str(value)
        return False, '下载器未返回任务标识'

    # ----------------------------------------------------------------- 事件

    @eventmanager.register(ChainEventType.TransferRenameBuild)
    def on_transfer_rename_build(self, event: Event) -> None:
        """整理渲染前改写季集号，让文件直接落到 Season 00。"""
        if not self._enabled or self._store is None:
            return
        data = getattr(event, 'event_data', None)
        rename_dict = getattr(data, 'rename_dict', None)
        if not isinstance(rename_dict, dict):
            return
        task = self._match_task(rename_dict)
        if not task:
            self._guard_unmatched(rename_dict)
            return
        apply_to_rename_dict(rename_dict, task.sxx_episode, task.sxx_name)
        logger.info(
            f'综艺衍生内容管家：{task.show_name} 整理改写为 S00E{task.sxx_episode:02d}'
            f'《{task.sxx_name}》（原 {task.title}）'
        )

    def _guard_unmatched(self, rename_dict: Dict[str, Any]) -> None:
        """兜底：能判定是衍生内容、但插件没有对应任务时，也不让它占用正片集号。

        这类文件通常是用户手动搜索下载的，或插件扫描后才补进来的。
        按站点编号把季号强制归零、集号放进 9xx 段，既保证不覆盖正片，
        也能在极影视/Emby 的特别季里看到；TMDB 补录后由正常任务接管。
        """
        if not _to_bool(self._config.get('guard_unmatched'), False):
            return
        if _to_int(rename_dict.get('season'), -1) == 0:
            return
        target = self._guard_target(rename_dict)
        if target is None:
            return
        episode, title = target
        apply_to_rename_dict(rename_dict, episode, title)
        logger.warning(
            f'综艺衍生内容管家：未匹配任务，兜底改写为 S00E{episode}《{title}》'
            f'（原 {rename_dict.get("original_name")}）'
        )

    @staticmethod
    def _guard_target(rename_dict: Dict[str, Any]) -> Optional[Tuple[int, str]]:
        """为未接管的衍生文件算出兜底特别集号与集名。"""
        original = str(rename_dict.get('original_name') or '')
        if not original:
            return None
        parsed = parse_title(original)
        if parsed is None or parsed.subtype is None:
            return None
        site_episode = parsed.site_episode or parsed.episode
        if not site_episode:
            # 没有编号就无法稳定落位，交回 MP 原流程
            return None

        episode = 900 + int(site_episode)
        word = subtype_label(parsed)
        show_season = parsed.season if parsed.season is not None else parsed.site_season
        if parsed.episode and show_season:
            title = f'第{show_season}季 第{parsed.episode}期{word}（TMDB未收录）'
        elif show_season:
            title = f'第{show_season}季 站点E{int(site_episode):02d} {word}（TMDB未收录）'
        else:
            title = f'{word}（TMDB未收录）'
        return episode, title

    @eventmanager.register(ChainEventType.TransferRename)
    def on_transfer_rename(self, event: Event) -> None:
        """渲染后兜底改写，处理模板里硬编码的季集写法。"""
        if not self._enabled or self._store is None:
            return
        data = getattr(event, 'event_data', None)
        if data is None or getattr(data, 'updated', False):
            return
        rename_dict = getattr(data, 'rename_dict', None)
        render_str = getattr(data, 'render_str', '') or ''
        if not isinstance(rename_dict, dict) or not render_str:
            return
        task = self._match_task(rename_dict)
        if task:
            new_str = rewrite_render_str(render_str, task.sxx_episode)
        elif _to_bool(self._config.get('guard_unmatched'), False) and _to_int(rename_dict.get('season'), -1) != 0:
            target = self._guard_target(rename_dict)
            if target is None:
                return
            new_str = rewrite_render_str(render_str, target[0])
        else:
            return
        if new_str == render_str:
            return
        data.updated = True
        data.updated_str = new_str
        data.source = '综艺衍生内容管家'

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        """整理完成：标记任务、刷新媒体库。"""
        if not self._enabled or self._store is None:
            return
        data = getattr(event, 'event_data', None)
        if data is None:
            return
        download_hash = str(getattr(data, 'download_hash', '') or '')
        task = self._store.find_by_hash(download_hash) if download_hash else None
        if not task:
            # 事件里的 meta/mediainfo 可能是 Pydantic 快照（字典），
            # 也可能是运行时对象，两种形态都要能取到值。
            mediainfo = getattr(data, 'mediainfo', None)
            meta = getattr(data, 'meta', None)
            tmdb_id = _to_int(_get_field(mediainfo, 'tmdb_id'), 0)
            season = _get_field(meta, 'season')
            episode = _get_field(meta, 'episode')
            if tmdb_id and season is not None and episode is not None:
                task = self._store.find_by_source(tmdb_id, _to_int(season, -1), _to_int(episode, -1))
        if not task:
            return

        self._store.update(task, status=STATUS_DONE, error='')
        logger.info(f'综艺衍生内容管家：{task.show_name} {task.target_label} 整理完成')

        if _to_bool(self._config.get('refresh_library'), True):
            self._refresh_library(task.show_name)

        if _to_bool(self._config.get('notify'), True):
            self.post_message(
                title='【综艺衍生内容】已入库',
                text=f'{task.show_name} {task.target_label}《{task.sxx_name}》已整理完成',
            )

    def _match_task(self, rename_dict: Dict[str, Any]) -> Optional[Task]:
        """从整理上下文里判断这条文件属于哪个衍生任务。

        安全边界：**只处理能确认是衍生内容的文件**。同剧正片的季集号可能
        与某条衍生任务完全相同，只按时段匹配会把正片改到 S00，那是灾难。
        所以先看源文件名里有没有衍生标记（加更/彩蛋/Plus 等），再按时段回连。
        """
        if self._store is None or not isinstance(rename_dict, dict):
            return None

        original = str(rename_dict.get('original_name') or '')
        parsed = parse_title(original) if original else None

        episodes: set[int] = set()
        seasons: set[int] = set()

        # 站点编号：整理阶段唯一可靠的连接点
        if parsed is not None:
            for value in (parsed.site_season, parsed.season):
                if value is not None:
                    seasons.add(value)
            for value in (parsed.site_episode, parsed.episode):
                if value is not None:
                    episodes.add(value)

        # rename_dict 里 MP 识别出的季集号，作为补充信号
        season = rename_dict.get('season')
        episode = rename_dict.get('episode')
        if isinstance(episode, (list, tuple)):
            episode = episode[0] if episode else None
        if season is not None and _to_int(season, -1) >= 0:
            seasons.add(_to_int(season, -1))
        if episode is not None and _to_int(episode, -1) >= 0:
            episodes.add(_to_int(episode, -1))

        if not episodes:
            return None

        title = str(rename_dict.get('name') or rename_dict.get('title') or '')

        for task in self._store.all():
            if task.status not in (STATUS_SUBMITTED, STATUS_QUEUED):
                continue
            if title and task.show_name not in title and title not in task.show_name:
                continue
            if not self._is_derived_file(parsed, original, task):
                continue
            if task.src_site_season is not None and seasons and task.src_site_season not in seasons:
                continue
            # 优先用站点编号回连，其次退回真实期号
            if task.src_site_episode is not None:
                if task.src_site_episode not in episodes:
                    continue
                return task
            if task.src_episode is not None and task.src_episode in episodes:
                return task
        return None

    @staticmethod
    def _is_derived_file(parsed: Any, original: str, task: Task) -> bool:
        """判断待整理文件确实是衍生内容，而不是同剧正片。

        判据只有两条，都很硬：
        1. 文件名能解析出衍生类型（加更/彩蛋/Plus/Special 等）；
        2. 文件名与任务记录的种子名完全一致（只是补了扩展名）。

        为什么不用模糊相似度：同一部剧同一集的「正片」和「加更」资源名
        往往只差一个 `Plus`，任何按重合度的判定都会把正片算进来。
        宁可漏改（文件落进正片季，肉眼可见、可手工处理），
        也不能误改（正片被塞进 Season 00，等于毁库）。
        """
        if parsed is not None and parsed.subtype is not None:
            return True
        if task.subtype_word and task.subtype_word.lower() in (original or '').lower():
            return True
        return _normalize_title(original) == _normalize_title(task.title)

    def _refresh_library(self, show_name: str) -> None:
        """通知媒体服务器刷新该剧，让 S00 新集尽快出现。"""
        try:
            from app.schemas.mediaserver import RefreshMediaItem

            item = RefreshMediaItem(title=show_name, type=MediaType.TV if HOST_AVAILABLE else None)
            self.chain.run_module('refresh_library_by_items', [item])
            logger.info(f'综艺衍生内容管家：已请求刷新媒体库 {show_name}')
        except Exception as err:  # noqa: BLE001
            logger.warning(f'综艺衍生内容管家：刷新媒体库失败：{err}')

    # ------------------------------------------------------------------- API

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                'path': '/scan',
                'endpoint': self.api_scan,
                'methods': ['POST'],
                'auth': 'bear',
                'summary': '立即扫描',
                'description': '按当前配置执行一轮扫描；dry_run=true 时只预演不下载',
            },
            {
                'path': '/tasks',
                'endpoint': self.api_tasks,
                'methods': ['GET'],
                'auth': 'bear',
                'summary': '任务列表',
                'description': '返回最近的衍生内容落地任务',
            },
            {
                'path': '/preview',
                'endpoint': self.api_preview,
                'methods': ['GET'],
                'auth': 'bear',
                'summary': '特别季预览',
                'description': '查看某部剧在 TMDB 特别季里的集号与集名，用于核对匹配依据',
            },
            {
                'path': '/misses',
                'endpoint': self.api_misses,
                'methods': ['GET'],
                'auth': 'bear',
                'summary': '等待补齐的内容',
                'description': '站点已发布但 TMDB 特别季尚未登记的内容，用于排查「为什么没下」',
            },
            {
                'path': '/clear',
                'endpoint': self.api_clear,
                'methods': ['POST'],
                'auth': 'bear',
                'summary': '清理记录',
                'description': '清理已完成或指定状态的记录',
            },
            {
                'path': '/sites',
                'endpoint': self.api_sites,
                'methods': ['GET'],
                'auth': 'bear',
                'summary': '可用站点 ID',
                'description': '列出宿主已接入的站点及其数字 ID，用于填写「限定站点 ID」',
            },
            {
                'path': '/sync',
                'endpoint': self.api_sync,
                'methods': ['POST'],
                'auth': 'bear',
                'summary': '立即同步订阅',
                'description': '从 MP 现有电视剧订阅中重新识别综艺并刷新跟踪列表',
            },
        ]

    def api_sync(self) -> Dict[str, Any]:
        try:
            count, note = self.refresh_subscriptions()
        except Exception as err:  # noqa: BLE001
            self._sync_error = str(err)
            return {'success': False, 'message': f'同步失败：{err}', 'data': {}}
        return {
            'success': True,
            'message': note,
            'data': {
                'auto': count,
                'manual': len(self._manual_shows),
                'shows': [
                    {'name': show.name, 'tmdb_id': show.tmdb_id} for show in self._shows
                ],
            },
        }

    def api_sites(self) -> Dict[str, Any]:
        catalog = self.site_catalog()
        wanted = self._configured_site_ids()
        return {
            'success': True,
            'message': f'共 {len(catalog)} 个站点，当前限定：{wanted or "全部"}',
            'data': catalog,
        }

    def api_scan(self, dry_run: Optional[bool] = None, show: str = '') -> Dict[str, Any]:
        """手动触发扫描。

        搜索要打站点，几部剧跑下来可能几分钟，不能让 HTTP 请求干等，
        所以放到后台线程执行，结果在插件页「上次扫描」里看。
        """
        if self._scanning:
            return {'success': False, 'message': '已有扫描在进行中，请稍后再试', 'data': {}}
        if self._store is None or self._index_store is None:
            return {'success': False, 'message': '插件尚未初始化，请先保存配置', 'data': {}}
        thread = threading.Thread(
            target=self.run_scan,
            kwargs={'dry_run': dry_run, 'only_show': show},
            name='VarietyPilotScan',
            daemon=True,
        )
        thread.start()
        scope = f'（仅 {show}）' if show else ''
        return {
            'success': True,
            'message': f'已开始扫描{scope}，完成后可在插件页查看结果',
            'data': {'started': True},
        }

    def api_records(self, limit: int = 200) -> Dict[str, Any]:
        """返回最近一次扫描的逐条明细，按「失败 → 跳过 → 等待 → 已提交」排序。"""
        order = {'失败': 0, '跳过': 1, '等待': 2, '待提交': 3, '预演': 4, '已提交': 5}
        items = sorted(
            self._last_records,
            key=lambda item: order.get(str(item.get('status') or ''), 9),
        )
        return {
            'success': True,
            'message': f'共 {len(items)} 条明细',
            'data': items[: max(1, limit)],
        }

    def api_tasks(self, limit: int = 100) -> Dict[str, Any]:
        if self._store is None:
            return {'success': False, 'message': '插件尚未初始化', 'data': []}
        rows = [
            {
                'show': task.show_name,
                'target': task.target_label,
                'name': task.sxx_name,
                'title': task.title,
                'status': task.status,
                'subtype': task.subtype,
                'site': task.site,
                'score': round(task.match_score, 1),
                'updated': datetime.fromtimestamp(task.updated_at).strftime('%m-%d %H:%M'),
            }
            for task in self._store.recent(limit=min(max(limit, 1), 500))
        ]
        return {'success': True, 'message': '', 'data': rows}

    def api_preview(self, tmdb_id: int = 0, limit: int = 60) -> Dict[str, Any]:
        if self._index_store is None or not tmdb_id:
            return {'success': False, 'message': '缺少 TMDB ID', 'data': []}
        try:
            index = self._index_store.load(int(tmdb_id))
        except Exception as err:  # noqa: BLE001
            return {'success': False, 'message': f'TMDB 特别季获取失败：{err}', 'data': []}
        rows = [
            {
                'episode': f'S00E{episode.episode:02d}',
                'name': episode.name,
                'air_date': episode.air_date or '',
                'subtype': episode.subtype or '',
            }
            for episode in index.episodes[: max(1, limit)]
        ]
        return {'success': True, 'message': f'共 {len(index.episodes)} 集', 'data': rows}

    def api_misses(self, limit: int = 50) -> Dict[str, Any]:
        if self._store is None:
            return {'success': False, 'message': '插件尚未初始化', 'data': []}
        rows = [
            {
                'show': miss.show_name,
                'title': miss.title,
                'season': miss.season,
                'episode': miss.episode,
                'subtype': miss.subtype,
                'reason': miss.reason,
                'count': miss.count,
                'last_seen': datetime.fromtimestamp(miss.last_seen).strftime('%m-%d %H:%M'),
            }
            for miss in self._store.misses(limit=min(max(limit, 1), 300))
        ]
        return {'success': True, 'message': f'共 {len(rows)} 条', 'data': rows}

    def api_clear(self, status: str = '') -> Dict[str, Any]:
        if self._store is None:
            return {'success': False, 'message': '插件尚未初始化'}
        wanted = [item.strip() for item in status.split(',') if item.strip()] or [STATUS_DONE, STATUS_SKIPPED]
        count = self._store.clear(wanted)
        if 'misses' in wanted:
            count += self._store.clear_misses()
        return {'success': True, 'message': f'已清理 {count} 条记录'}

    # ------------------------------------------------------------------- 表单

    def _site_items(self) -> List[Dict[str, Any]]:
        """下拉选项：宿主站点库 + 配置里已有但库里找不到的 ID（保留以便用户自查）。"""
        items: List[Dict[str, Any]] = []
        seen: set[int] = set()
        for row in self.site_catalog():
            label = f'{row["name"]}（{row["id"]}）' if row['name'] else str(row['id'])
            if row['public']:
                label += ' · 公开站'
            items.append({'title': label, 'value': row['id']})
            seen.add(row['id'])
        for site_id in self._configured_site_ids():
            if site_id in seen:
                continue
            items.append({'title': f'未知站点（{site_id}）', 'value': site_id})
        return items

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            self._switch('enabled', '启用插件', 4),
                            self._switch('dry_run', '预演模式（只匹配不下载）', 4),
                            self._switch('notify', '发送通知', 4),
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'shows',
                                            'label': '跟踪的综艺',
                                            'rows': 5,
                                            'placeholder': '现在就出发|231620|2023\n我家那闺女|126490|别名1,别名2',
                                            'hint': '每行一部：剧名|TMDB ID|可选（年份或别名）。TMDB ID 为剧集页地址 /tv/ 后面的数字。',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '扫描周期',
                                            'placeholder': '5 位 cron 表达式',
                                            'hint': '点右侧按钮可视化选择；默认每 30 分钟扫描一次',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'site_ids',
                                            'label': '限定站点',
                                            'items': self._site_items(),
                                            'multiple': True,
                                            'chips': True,
                                            'closable-chips': True,
                                            'clearable': True,
                                            'hint': '不选表示全部站点；只影响衍生内容的搜索范围',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            self._switch('sync_subscribe', '自动同步 MP 订阅中的综艺', 4),
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'sync_interval_minutes',
                                            'label': '订阅同步间隔(分钟)',
                                            'type': 'number',
                                            'hint': '默认 30 分钟；0 表示每轮扫描都同步',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_scope',
                                            'label': '同步范围',
                                            'items': [
                                                {'title': '仅综艺（按 TMDB 类型判断）', 'value': 'variety'},
                                                {'title': '全部电视剧订阅', 'value': 'all'},
                                            ],
                                            'hint': '判断依据是 TMDB 的真人秀/脱口秀类型',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'sync_exclude',
                                            'label': '不跟踪的 TMDB ID',
                                            'placeholder': '例：231620,126490',
                                            'hint': '订阅里有、但不想让插件处理的剧；多个用英文逗号分隔',
                                            'persistent-hint': True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {'model': 'min_seeders', 'label': '最小做种数', 'type': 'number'},
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {'model': 'min_size_gb', 'label': '最小体积(GB)', 'type': 'number'},
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {'model': 'max_size_gb', 'label': '最大体积(GB)', 'type': 'number'},
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'prefer_resolution',
                                            'label': '偏好清晰度',
                                            'items': ['2160p', '1080p', '720p'],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            self._switch('refresh_library', '入库后刷新媒体库', 4),
                            self._switch('overwrite_existing', '允许重复下载已有集', 4),
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'index_ttl_hours',
                                            'label': '特别季缓存(小时)',
                                            'type': 'number',
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            self._switch('guard_unmatched', '未接管资源也强制进特别季', 6),
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'density': 'compact',
                                            'text': (
                                                '开启后，手动下载或插件未接管的衍生文件也会被改写为 '
                                                'Season 00 的 9xx 集，避免占用正片集号被覆盖；'
                                                '关闭时这类文件按 MP 原流程落位。'
                                            ),
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        'component': 'VAlert',
                        'props': {
                            'type': 'info',
                            'variant': 'tonal',
                            'text': (
                                '工作原理：插件独立搜索衍生资源，按 TMDB 特别季的真实集号落位，'
                                '文件直接整理到「剧名 (年份)/Season 00/剧名 - S00E集号 - 集名」，'
                                '不占用正片集号，极影视与 Emby 按特别篇正常刮削。'
                                '建议先开预演模式跑一轮，确认匹配无误后再关闭预演。'
                            ),
                        },
                    },
                ],
            }
        ], {
            **DEFAULT_CONFIG,
            **self._config,
            # 旧版把站点存成逗号串，多选控件只认数组，这里统一一次
            'site_ids': self._configured_site_ids(),
        }

    @staticmethod
    def _switch(model: str, label: str, width: int = 4) -> dict:
        return {
            'component': 'VCol',
            'props': {'cols': 12, 'md': width},
            'content': [
                {'component': 'VSwitch', 'props': {'model': model, 'label': label}}
            ],
        }

    # ------------------------------------------------------------------- 页面

    def get_page(self) -> List[dict]:
        stats = self._store.stats() if self._store else {}
        recent = self._store.recent(limit=15) if self._store else []

        head = self._card(
            '运行概况',
            [
                ('跟踪剧集', f'{len(self._shows)} 部'),
                ('总任务', f'{stats.get("total", 0)} 条'),
                ('待入库', f'{stats.get(STATUS_SUBMITTED, 0) + stats.get(STATUS_QUEUED, 0)} 条'),
                ('已入库', f'{stats.get(STATUS_DONE, 0)} 条'),
                (
                    '上次扫描',
                    datetime.fromtimestamp(self._last_scan_at).strftime('%Y-%m-%d %H:%M')
                    if self._last_scan_at
                    else '尚未扫描',
                ),
                ('预演模式', '开' if _to_bool(self._config.get('dry_run'), True) else '关'),
            ],
        )

        rows: List[dict] = [
            {
                'component': 'VListItem',
                'props': {'density': 'compact'},
                'content': [
                    {
                        'component': 'VListItemTitle',
                        'text': f'{task.show_name} {task.target_label} · {task.sxx_name}',
                    },
                    {
                        'component': 'VListItemSubtitle',
                        'text': f'{task.status} ｜ {task.title[:60]}',
                    },
                ],
            }
            for task in recent
        ] or [{'component': 'VAlert', 'props': {'type': 'info', 'variant': 'tonal', 'text': '暂无任务记录'}}]

        manual_ids = {show.tmdb_id for show in self._manual_shows}
        shows_rows = [
            {
                'component': 'VListItem',
                'props': {'density': 'compact'},
                'content': [
                    {
                        'component': 'VListItemTitle',
                        'text': (
                            f'{show.name}（TMDB {show.tmdb_id}）'
                            f'　{"手动" if show.tmdb_id in manual_ids else "订阅"}'
                        ),
                    },
                    {
                        'component': 'VListItemSubtitle',
                        'text': (
                            f'别名：{", ".join(show.aliases) or "无"}'
                            f' ｜ 站点：{", ".join(str(s) for s in show.sites) or "全部"}'
                        ),
                    },
                ],
            }
            for show in self._shows
        ] or [{'component': 'VAlert', 'props': {'type': 'warning', 'variant': 'tonal', 'text': '尚未配置跟踪剧集'}}]

        waits = self._store.misses(limit=10) if self._store else []
        wait_rows: List[dict] = [
            {
                'component': 'VListItem',
                'props': {'density': 'compact'},
                'content': [
                    {
                        'component': 'VListItemTitle',
                        'text': f'{miss.show_name} · {miss.title[:70]}',
                    },
                    {
                        'component': 'VListItemSubtitle',
                        'text': (
                            f'第{miss.episode}期 / 类型 {miss.subtype or "-"}'
                            f' ｜ {miss.reason} ｜ 已见 {miss.count} 次'
                        ),
                    },
                ],
            }
            for miss in waits
        ] or [
            {
                'component': 'VAlert',
                'props': {'type': 'success', 'variant': 'tonal', 'text': '没有滞留的衍生内容'},
            }
        ]

        return [
            head,
            self._sync_card(),
            {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'text': f'跟踪列表（{len(self._shows)} 部，手动优先）',
                    },
                    {'component': 'VCardText', 'content': shows_rows},
                ],
            },
            {
                'component': 'VCard',
                'props': {'variant': 'tonal', 'class': 'mb-4'},
                'content': [
                    {'component': 'VCardTitle', 'text': '最近任务'},
                    {'component': 'VCardText', 'content': rows},
                ],
            },
            {
                'component': 'VCard',
                'props': {'variant': 'tonal', 'class': 'mb-4'},
                'content': [
                    {'component': 'VCardTitle', 'text': '等待 TMDB 补齐'},
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'density': 'compact',
                                    'class': 'mb-2',
                                    'text': (
                                        '以下内容站点已发布，但 TMDB 特别季里还没有对应集。'
                                        '插件每轮都会重试，TMDB 补齐后自动下载落位，无需手工处理。'
                                    ),
                                },
                            },
                            *wait_rows,
                        ],
                    },
                ],
            },
            self._card('上次扫描结果', [('', self._last_report or '尚未执行扫描')]),
            self._records_card(),
            self._site_card(),
        ]

    def _records_card(self) -> dict:
        """逐条列出每条资源的落位、得分和去向，避免「明细无处可查」。"""
        title = '最近扫描明细'
        if not self._last_records:
            return {
                'component': 'VCard',
                'props': {'class': 'mb-3', 'variant': 'outlined'},
                'content': [
                    {'component': 'VCardTitle', 'text': title},
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'density': 'compact',
                                    'text': '尚未扫描。执行一次扫描后，这里会按「失败 → 跳过 → 等待 → 已提交」列出每一条资源。',
                                },
                            }
                        ],
                    },
                ],
            }

        counts: Dict[str, int] = {}
        for record in self._last_records:
            status = str(record.get('status') or '未知')
            counts[status] = counts.get(status, 0) + 1
        summary = ' ｜ '.join(f'{status} {count}' for status, count in counts.items())
        order = {'失败': 0, '跳过': 1, '等待': 2, '待提交': 3, '预演': 4, '已提交': 5}
        ordered = sorted(
            self._last_records,
            key=lambda item: order.get(str(item.get('status') or ''), 9),
        )
        shown = ordered[:150]
        content: List[dict] = [
            {
                'component': 'VAlert',
                'props': {
                    'type': 'warning' if counts.get('失败') else 'info',
                    'variant': 'tonal',
                    'density': 'compact',
                    'class': 'mb-2',
                    'text': f'共 {len(self._last_records)} 条 ｜ {summary}',
                },
            }
        ]
        for record in shown:
            status = str(record.get('status') or '')
            reason = str(record.get('reason') or '')
            target = str(record.get('target') or '-')
            sxx_name = str(record.get('sxx_name') or '')
            head = f'{status} ｜ {target} ← {record.get("src") or "-"} ｜ {str(record.get("title") or "")[:80]}'
            tail = (
                f'得分 {record.get("score")} ｜ {record.get("size_gb")}GB ｜ '
                f'做种 {record.get("seeders")}'
            )
            if sxx_name:
                tail += f' ｜ TMDB：{sxx_name}'
            if reason:
                tail += f' ｜ {reason}'
            content.append(
                {
                    'component': 'VListItem',
                    'props': {'density': 'compact'},
                    'content': [
                        {
                            'component': 'VListItemTitle',
                            'props': {'class': 'text-body-2'},
                            'text': head,
                        },
                        {
                            'component': 'VListItemSubtitle',
                            'props': {'class': 'text-caption'},
                            'text': tail,
                        },
                    ],
                }
            )
        if len(ordered) > len(shown):
            content.append(
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'density': 'compact',
                        'class': 'mt-2',
                        'text': f'仅显示前 {len(shown)} 条，完整 {len(ordered)} 条可通过插件接口 /scan 结果获取。',
                    },
                }
            )
        return {
            'component': 'VCard',
            'props': {'class': 'mb-3', 'variant': 'outlined'},
            'content': [
                {'component': 'VCardTitle', 'text': title},
                {'component': 'VCardText', 'content': content},
            ],
        }

    def _sync_card(self) -> dict:
        """订阅同步状态：让用户看得见「同步到了什么、为什么少了几部」。"""
        enabled = _to_bool(self._config.get('sync_subscribe'), True)
        content: List[dict] = []
        if not enabled:
            content.append(
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'density': 'compact',
                        'text': '未开启「自动同步 MP 订阅」。开启后插件会从现有订阅里挑出综艺自动跟踪。',
                    },
                }
            )
        else:
            last = (
                datetime.fromtimestamp(self._sync_at).strftime('%Y-%m-%d %H:%M')
                if self._sync_at
                else '尚未同步'
            )
            interval = self._sync_interval_minutes()
            cadence = f'每 {interval} 分钟同步' if interval > 0 else '每轮扫描都同步'
            content.append(
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'success' if self._sync_at else 'info',
                        'variant': 'tonal',
                        'density': 'compact',
                        'class': 'mb-2',
                        'text': (
                            f'上次同步：{last} ｜ {cadence} ｜ 订阅识别 {len(self._auto_shows)} 部 ｜ '
                            f'手动配置 {len(self._manual_shows)} 部 ｜ '
                            f'范围：{"仅综艺" if str(self._config.get("sync_scope") or "variety") != "all" else "全部电视剧"}'
                        ),
                    },
                }
            )
            for reason in self._sync_skipped[:8]:
                content.append(
                    {
                        'component': 'VAlert',
                        'props': {
                            'type': 'warning',
                            'variant': 'tonal',
                            'density': 'compact',
                            'class': 'mb-2',
                            'text': reason,
                        },
                    }
                )
            if self._sync_error:
                content.append(
                    {
                        'component': 'VAlert',
                        'props': {
                            'type': 'error',
                            'variant': 'tonal',
                            'density': 'compact',
                            'text': self._sync_error,
                        },
                    }
                )
        return {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {'component': 'VCardTitle', 'text': '订阅同步'},
                {'component': 'VCardText', 'content': content},
            ],
        }

    def _site_card(self) -> dict:
        """「限定站点 ID」对照表：直接列出数字 ID，省得用户去翻接口。"""
        catalog = self.site_catalog()
        wanted = self._configured_site_ids()
        if not catalog:
            return self._card(
                '可用站点 ID',
                [('', '读取站点列表失败，请在「设定 → 站点」里查看，或稍后刷新页面重试')],
            )

        picked = ', '.join(str(item) for item in wanted) or '全部站点'
        rows: List[dict] = []
        for row in catalog:
            flag = '已选' if row['id'] in wanted else ('公开站' if row['public'] else '')
            text = f'{row["id"]}　{row["name"]}　{row["domain"]}'
            if flag:
                text = f'{text}（{flag}）'
            rows.append(
                {
                    'component': 'VListItem',
                    'props': {'density': 'compact'},
                    'content': [{'component': 'VListItemTitle', 'text': text}],
                }
            )

        content: List[dict] = [
            {
                'component': 'VAlert',
                'props': {
                    'type': 'info',
                    'variant': 'tonal',
                    'density': 'compact',
                    'class': 'mb-2',
                    'text': f'当前生效：{picked}。把左侧数字填进「限定站点 ID」，多个用英文逗号分隔。',
                },
            },
            *rows,
        ]
        for warning in self._site_warnings():
            content.insert(
                0,
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'warning',
                        'variant': 'tonal',
                        'density': 'compact',
                        'class': 'mb-2',
                        'text': warning,
                    },
                },
            )
        return {
            'component': 'VCard',
            'props': {'variant': 'outlined', 'class': 'mb-4'},
            'content': [
                {'component': 'VCardTitle', 'text': '可用站点 ID'},
                {'component': 'VCardText', 'content': content},
            ],
        }

    @staticmethod
    def _card(title: str, items: List[Tuple[str, str]]) -> dict:
        content = [
            {'component': 'VCardTitle', 'text': title},
            {
                'component': 'VCardText',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [
                                    {
                                        'component': 'div',
                                        'props': {'class': 'text-body-2'},
                                        'text': f'{label}：{value}' if label else value,
                                    }
                                ],
                            }
                            for label, value in items
                        ],
                    }
                ],
            },
        ]
        return {'component': 'VCard', 'props': {'variant': 'outlined', 'class': 'mb-4'}, 'content': content}
