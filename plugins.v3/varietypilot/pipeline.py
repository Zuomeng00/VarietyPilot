"""扫描编排：搜索 → 分类 → 匹配特别季 → 择优 → 提交下载。

搜索与下载通过回调注入，便于脱离 MoviePilot 单独测试决策逻辑。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from .sxxindex import MIN_ACCEPT_SCORE, MatchResult, SpecialIndex, match_for_title
from .store import (
    STATUS_QUEUED,
    STATUS_SKIPPED,
    STATUS_SUBMITTED,
    MissRecord,
    Task,
    TaskStore,
)
from .titles import ParsedTitle

logger = logging.getLogger(__name__)

_RE_RESOLUTION = re.compile(r'(?<!\d)(2160p|1080p|1080i|720p|480p|4k|8k)(?!\d)', re.IGNORECASE)
_RESOLUTION_RANK = {'2160p': 4, '4k': 4, '8k': 5, '1080p': 3, '1080i': 3, '720p': 2, '480p': 1}


@dataclass
class TrackedShow:
    """一部被跟踪的综艺。"""

    name: str
    tmdb_id: int
    year: str = ''
    sites: list[int] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def search_names(self) -> list[str]:
        names = [self.name, *self.aliases]
        # 去重保序
        seen: set[str] = set()
        result = []
        for item in names:
            key = item.strip()
            if key and key not in seen:
                seen.add(key)
                result.append(key)
        return result


@dataclass
class Options:
    """扫描参数。"""

    min_seeders: int = 1
    min_size_gb: float = 0.05
    max_size_gb: float = 20.0
    prefer_resolution: str = '1080p'
    allow_partial: bool = True       # 允许上/下集分别落位
    overwrite_existing: bool = False


@dataclass
class Candidate:
    """一条待下载的衍生资源。"""

    title: str
    site: str
    size: int
    seeders: int
    context: Any
    parsed: ParsedTitle
    match: MatchResult
    description: str = ''
    free: bool = False

    @property
    def resolution_rank(self) -> int:
        found = _RE_RESOLUTION.search(self.title)
        if not found:
            return 0
        return _RESOLUTION_RANK.get(found.group(1).lower(), 0)

    @property
    def size_gb(self) -> float:
        return self.size / (1024 ** 3) if self.size else 0.0


@dataclass
class ScanReport:
    """一次扫描的结果汇总。"""

    show: str
    searched: int = 0
    derived_found: int = 0
    matched: int = 0
    submitted: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f'{self.show}：搜索 {self.searched} 条，衍生 {self.derived_found} 条，'
            f'命中特别篇 {self.matched} 条，提交 {self.submitted}，跳过 {self.skipped}，失败 {self.failed}'
        )


def _context_title(context: Any) -> str:
    """从 MP 的 Context 里取种子标题。"""
    torrent = getattr(context, 'torrent_info', None)
    if torrent is None:
        return ''
    for attr in ('title', 'name'):
        value = getattr(torrent, attr, None)
        if value:
            return str(value)
    return ''


def _context_description(context: Any) -> str:
    """站点副标题。中文期号与「加更/彩蛋」这类标记通常写在这里。"""
    torrent = getattr(context, 'torrent_info', None)
    if torrent is None:
        return ''
    return str(getattr(torrent, 'description', '') or '')


def _context_free(context: Any) -> bool:
    """站点是否标记免费（下载倍率为 0）。未知按不免费处理。"""
    torrent = getattr(context, 'torrent_info', None)
    if torrent is None:
        return False
    factor = getattr(torrent, 'downloadvolumefactor', None)
    try:
        return factor is not None and float(factor) == 0.0
    except (TypeError, ValueError):
        return False


def _context_declared_id(context: Any) -> str:
    """站点页面声明的 TMDB 标识（若站点支持）。

    有值时可以直接当作硬过滤：英文剧名容易撞名，
    站点既然标了 TMDB ID，就不用再靠标题猜。
    """
    torrent = getattr(context, 'torrent_info', None)
    if torrent is None:
        return ''
    media_id = getattr(torrent, 'media_id', None)
    return str(media_id).strip() if media_id else ''


def _context_site(context: Any) -> str:
    torrent = getattr(context, 'torrent_info', None)
    for attr in ('site_name', 'site'):
        value = getattr(torrent, attr, None)
        if value:
            return str(value)
    return ''


def _context_size(context: Any) -> int:
    torrent = getattr(context, 'torrent_info', None)
    value = getattr(torrent, 'size', 0) if torrent is not None else 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _context_seeders(context: Any) -> int:
    torrent = getattr(context, 'torrent_info', None)
    value = getattr(torrent, 'seeders', 0) if torrent is not None else 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _context_hash(context: Any) -> str:
    torrent = getattr(context, 'torrent_info', None)
    for attr in ('enclosure', 'page_url', 'description'):
        value = getattr(torrent, attr, None)
        if value and attr == 'enclosure':
            return str(value)
    return ''


def rank_key(candidate: Candidate, prefer_resolution: str = '1080p') -> tuple:
    """择优排序：免费优先 → 目标清晰度 → 做种数 → 体积（小者优先）。

    PT 站点流量金贵，同一集有免费种时优先抓免费。
    """
    prefer_rank = _RESOLUTION_RANK.get(prefer_resolution.lower(), 3)
    # 与偏好一致得 0，高于偏好得 1（宁可要 1080p 而不是 2160p 的加更），低于偏好得 2
    if candidate.resolution_rank == prefer_rank:
        resolution_score = 0
    elif candidate.resolution_rank > prefer_rank:
        resolution_score = 1
    elif candidate.resolution_rank > 0:
        resolution_score = 2
    else:
        resolution_score = 3
    return (0 if candidate.free else 1, resolution_score, -candidate.seeders, candidate.size or 0)


def filter_candidate(candidate: Candidate, options: Options) -> Optional[str]:
    """返回跳过原因；None 表示通过。"""
    if candidate.seeders < options.min_seeders:
        return f'做种 {candidate.seeders} 少于阈值 {options.min_seeders}'
    size_gb = candidate.size_gb
    if size_gb and size_gb < options.min_size_gb:
        return f'体积 {size_gb:.2f}GB 小于下限 {options.min_size_gb}GB'
    if size_gb and size_gb > options.max_size_gb:
        return f'体积 {size_gb:.2f}GB 超过上限 {options.max_size_gb}GB'
    if candidate.parsed.is_pack:
        return '整季包，衍生内容不处理整季包'
    return None


def build_candidates(
    contexts: list[Any],
    show: TrackedShow,
    index: SpecialIndex,
    require_subtype: bool = True,
) -> tuple[list[Candidate], int, int, list[tuple[str, ParsedTitle]]]:
    """把搜索结果转成候选列表。

    资源名给站点编号，站点副标题给中文期号与衍生类型，两者合并解析。

    :return: (候选, 衍生条数, 成功匹配条数, 未匹配的衍生标题及解析结果)
    """
    names = show.search_names
    candidates: list[Candidate] = []
    unmatched: list[tuple[str, ParsedTitle]] = []
    derived = 0
    matched = 0
    for context in contexts:
        title = _context_title(context)
        if not title:
            continue
        declared = _context_declared_id(context)
        if declared and declared != str(show.tmdb_id):
            # 站点已声明 TMDB 标识且对不上，说明是别的剧，直接排除
            continue
        description = _context_description(context)
        result, parsed = match_for_title(title, names, index, description=description)
        is_derived = parsed.subtype is not None
        if is_derived:
            derived += 1
        if result is None:
            if is_derived:
                unmatched.append((title, parsed))
            continue
        # 正片也可能因为季号+期号吻合而命中特别季，必须挡掉。
        # 只有带衍生标记、或得分高到不可能是正片的条目才允许下载，
        # 否则插件会把正片从用户的正片订阅里抢走。
        if require_subtype and not is_derived and result.score < MIN_ACCEPT_SCORE + 2.5:
            logger.debug(f'综艺衍生内容管家：跳过无衍生标记的条目《{title}》（{result.score:.1f} 分）')
            continue
        matched += 1
        candidates.append(
            Candidate(
                title=title,
                site=_context_site(context),
                size=_context_size(context),
                seeders=_context_seeders(context),
                context=context,
                parsed=parsed,
                match=result,
                description=description,
                free=_context_free(context),
            )
        )
    return candidates, derived, matched, unmatched


def plan_tasks(
    candidates: list[Candidate],
    store: TaskStore,
    show: TrackedShow,
    options: Options,
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """按特别集号分组择优，返回 (待下载, 跳过项及原因)。"""
    grouped: dict[int, list[Candidate]] = {}
    skipped: list[tuple[Candidate, str]] = []

    for candidate in candidates:
        reason = filter_candidate(candidate, options)
        if reason:
            skipped.append((candidate, reason))
            continue
        grouped.setdefault(candidate.match.episode, []).append(candidate)

    chosen: list[Candidate] = []
    for episode, items in grouped.items():
        existing = store.get(show.tmdb_id, episode)
        if existing and existing.status in (STATUS_SUBMITTED, STATUS_QUEUED, 'done'):
            if not options.overwrite_existing:
                skipped.append((items[0], f'S00E{episode:02d} 已处理（{existing.status}）'))
                continue
        best = sorted(items, key=lambda item: rank_key(item, options.prefer_resolution))[0]
        chosen.append(best)
        for other in items:
            if other is not best:
                skipped.append((other, f'S00E{episode:02d} 已有更优版本'))
    return chosen, skipped


def make_task(candidate: Candidate, show: TrackedShow) -> Task:
    """由候选生成待提交任务。"""
    return Task(
        tmdb_id=show.tmdb_id,
        show_name=show.name,
        sxx_episode=candidate.match.episode,
        sxx_name=candidate.match.name,
        title=candidate.title,
        src_season=candidate.parsed.season,
        src_episode=candidate.parsed.episode,
        src_site_season=candidate.parsed.site_season,
        src_site_episode=candidate.parsed.site_episode,
        subtype=candidate.parsed.subtype,
        subtype_word=candidate.parsed.subtype_word or '',
        modifier=candidate.parsed.modifier,
        part=candidate.parsed.part,
        site=candidate.site,
        size=candidate.size,
        seeders=candidate.seeders,
        match_score=candidate.match.score,
    )


SearchFn = Callable[[str, list[int]], list[Any]]
DownloadFn = Callable[[Candidate], tuple[bool, str]]


def scan_show(
    show: TrackedShow,
    index: SpecialIndex,
    store: TaskStore,
    options: Options,
    search_fn: SearchFn,
    download_fn: Optional[DownloadFn] = None,
    dry_run: bool = False,
) -> ScanReport:
    """扫描单部剧并提交下载。"""
    report = ScanReport(show=show.name)
    if not index.episodes:
        report.details.append(f'{show.name}：TMDB 没有特别季，跳过')
        return report

    # 站点资源名常用 TMDB 英文标题（Natural High / Up All Friday Night），
    # 把索引里取到的名字并进搜索词，否则中文名一条都搜不到。
    merged_aliases = list(show.aliases)
    for name in index.search_names:
        if name and name != show.name and name not in merged_aliases:
            merged_aliases.append(name)
    if merged_aliases != show.aliases:
        show = replace(show, aliases=merged_aliases)

    contexts: list[Any] = []
    for name in show.search_names:
        try:
            contexts.extend(search_fn(name, show.sites))
        except Exception as err:  # noqa: BLE001
            report.details.append(f'{show.name}：搜索「{name}」失败 {err}')
            logger.warning(f'综艺衍生内容管家：搜索 {name} 失败：{err}')

    report.searched = len(contexts)
    if not contexts:
        return report

    candidates, report.derived_found, report.matched, unmatched = build_candidates(contexts, show, index)

    # 扫到了衍生内容但 TMDB 特别季里没有登记：记录下来等下一轮，不静默丢弃
    for title, parsed in unmatched[:40]:
        store.record_miss(
            MissRecord(
                tmdb_id=show.tmdb_id,
                show_name=show.name,
                title=title,
                season=parsed.season,
                episode=parsed.episode,
                subtype=parsed.subtype,
                reason='TMDB 特别季暂无对应集，等待补齐',
            )
        )
    if unmatched:
        report.details.append(f'{len(unmatched)} 条衍生内容在 TMDB 特别季中暂无对应集，已记录等待')

    chosen, skipped = plan_tasks(candidates, store, show, options)
    report.skipped = len(skipped)
    for candidate, reason in skipped[:20]:
        report.details.append(f'跳过《{candidate.title}》：{reason}')

    for candidate in chosen:
        task = make_task(candidate, show)
        if dry_run:
            report.details.append(
                f'[预演] {task.target_label} ← 《{candidate.title}》'
                f'（{candidate.match.score:.1f} 分，{candidate.size_gb:.2f}GB，{candidate.seeders} 做种）'
            )
            continue
        store.upsert(task)
        if download_fn is None:
            continue
        try:
            ok, message = download_fn(candidate)
        except Exception as err:  # noqa: BLE001
            ok, message = False, str(err)
        if ok:
            store.update(task, status=STATUS_SUBMITTED, download_hash=message)
            report.submitted += 1
            report.details.append(f'已提交 {task.target_label} ← 《{candidate.title}》')
        else:
            store.update(task, status=STATUS_SKIPPED, error=message)
            report.failed += 1
            report.details.append(f'提交失败 {task.target_label}：《{candidate.title}》{message}')
    return report
