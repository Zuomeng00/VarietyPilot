"""综艺衍生内容管家 —— 把加更、彩蛋、纯享等衍生内容准确落到 TMDB 特别季。

设计要点：
1. 衍生内容由插件独立搜索与下载，不与正片订阅抢位，也不影响订阅完成度判定；
2. 落位依靠 TMDB 特别季的真实集号，不做「顺延到尾部」的猜测；
3. 在整理渲染前改写季集号，文件直接落到 Season 00，不搬动已入库文件。
"""

from __future__ import annotations

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
from .titles import parse_title

logger = logging.getLogger(__name__)

PLUGIN_ID = 'VarietyPilot'

DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': False,
    'shows': '',
    'cron': '*/30 * * * *',
    'site_ids': '',
    'min_seeders': 1,
    'min_size_gb': 0.05,
    'max_size_gb': 20.0,
    'prefer_resolution': '1080p',
    'dry_run': True,
    'notify': True,
    'refresh_library': True,
    'overwrite_existing': False,
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
    plugin_desc = '把加更/彩蛋/纯享等衍生内容按 TMDB 特别季准确落位到 Season 00，不占正片集号，极影视与 Emby 均可正常入库。'
    plugin_icon = 'mdi-television-classic'
    plugin_version = '2.0.0'
    plugin_author = '阿白'
    author_url = ''
    plugin_config_prefix = 'varietypilot_'
    plugin_order = 5
    auth_level = 1

    # ---- 运行时状态（不在 __init__ 做任何 IO）
    _enabled: bool = False
    _config: Dict[str, Any]
    _shows: List[TrackedShow]
    _store: Optional[TaskStore] = None
    _index_store: Optional[SpecialIndexStore] = None
    _data_path: Optional[Path] = None
    _last_scan_at: float = 0.0
    _last_report: str = ''
    _scanning: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._options = Options()
        self._shows = []
        self._config = dict(DEFAULT_CONFIG)
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
        self._shows = self._parse_shows(str(merged.get('shows') or ''), str(merged.get('site_ids') or ''))

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
        logger.info(
            f'综艺衍生内容管家：配置已生效，状态={"启用" if self._enabled else "停用"}，'
            f'跟踪 {len(self._shows)} 部剧，预演={"开" if _to_bool(merged.get("dry_run"), True) else "关"}'
        )

    def get_state(self) -> bool:
        return bool(self._enabled)

    @staticmethod
    def _parse_shows(text: str, site_text: str) -> List[TrackedShow]:
        """解析剧列表，每行 `剧名|TMDBID|年份`, 每行 `剧名|TMDBID|别名1,别名2` 也可用。"""
        shows: List[TrackedShow] = []
        sites = [_to_int(item, 0) for item in (site_text or '').split(',') if _to_int(item, 0) > 0]
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

    def stop_service(self) -> None:
        """释放扫描状态。插件不建自建线程，无需额外回收。"""
        self._scanning = False

    # ------------------------------------------------------------------- 扫描

    def scan_task(self) -> None:
        """定时任务入口。"""
        if not self._enabled or self._scanning:
            return
        self.run_scan(dry_run=_to_bool(self._config.get('dry_run'), True))

    def run_scan(self, dry_run: Optional[bool] = None, only_show: str = '') -> Tuple[int, str]:
        """执行一轮扫描，返回 (提交数量, 文本汇总)。"""
        if self._store is None or self._index_store is None:
            return 0, '插件尚未初始化，请先保存配置'
        if not self._shows:
            return 0, '未配置跟踪剧集，请在插件设置里填写「剧名|TMDB ID」'

        dry = _to_bool(self._config.get('dry_run'), True) if dry_run is None else dry_run
        targets = [show for show in self._shows if not only_show or show.name == only_show]
        if not targets:
            return 0, f'未找到剧集：{only_show}'

        with self._lock:
            self._scanning = True
        lines: List[str] = []
        submitted = 0
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
                lines.append(report.line())
                for detail in report.details[:12]:
                    lines.append(f'  · {detail}')
                if len(report.details) > 12:
                    lines.append(f'  · 其余 {len(report.details) - 12} 条明细请在插件页面查看')
            self._last_scan_at = time.time()
            self._last_report = '\n'.join(lines)
            summary = '\n'.join(lines)
            if _to_bool(self._config.get('notify'), True) and (submitted or not dry):
                title = '【综艺衍生内容】预演结果' if dry else '【综艺衍生内容】已提交下载'
                self.post_message(title=title, text=summary[:1500])
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

    def _download(self, candidate: Candidate) -> Tuple[bool, str]:
        from app.chain.download import DownloadChain

        chain = DownloadChain()
        try:
            value = chain.download_single(
                context=candidate.context,
                source='综艺衍生内容',
                return_detail=True,
            )
        except TypeError:
            value = chain.download_single(candidate.context)
        if isinstance(value, tuple):
            download_hash = value[0] if value else None
            message = value[1] if len(value) > 1 else ''
            if download_hash:
                return True, str(download_hash)
            return False, str(message or '下载器未返回任务标识')
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
            return
        apply_to_rename_dict(rename_dict, task.sxx_episode, task.sxx_name)
        logger.info(
            f'综艺衍生内容管家：{task.show_name} 整理改写为 S00E{task.sxx_episode:02d}'
            f'《{task.sxx_name}》（原 {task.title}）'
        )

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
        if not task:
            return
        new_str = rewrite_render_str(render_str, task.sxx_episode)
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
        ]

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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '扫描周期（Cron）',
                                            'placeholder': '*/30 * * * *',
                                            'hint': '默认每 30 分钟扫描一次',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'site_ids',
                                            'label': '限定站点 ID',
                                            'placeholder': '留空表示全部站点',
                                            'hint': '多个用英文逗号分隔，站点 ID 见 MP 站点管理',
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
        ], dict(DEFAULT_CONFIG)

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

        shows_rows = [
            {
                'component': 'VListItem',
                'props': {'density': 'compact'},
                'content': [
                    {'component': 'VListItemTitle', 'text': f'{show.name}（TMDB {show.tmdb_id}）'},
                    {'component': 'VListItemSubtitle', 'text': f'别名：{", ".join(show.aliases) or "无"}'},
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
            self._card('跟踪列表', [('', '\n'.join(f'{s.name} / TMDB {s.tmdb_id}' for s in self._shows) or '未配置')]),
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
        ]

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
