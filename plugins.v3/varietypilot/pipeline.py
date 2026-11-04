"""扫描编排：搜索 → 分类 → 匹配特别季 → 择优 → 提交下载。

搜索与下载通过回调注入，便于脱离 MoviePilot 单独测试决策逻辑。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from .sxxindex import MIN_ACCEPT_SCORE, MatchResult, SpecialIndex, rank_specials
from .store import (
    STATUS_QUEUED,
    STATUS_SKIPPED,
    STATUS_SUBMITTED,
    MissRecord,
    Task,
    TaskStore,
)
from .titles import ParsedTitle, parse_release, strip_show_name

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
    # 同一份资源可落位的多个候选集号，按分数降序；落位环节取其中一个未被占用的
    options: list[MatchResult] = field(default_factory=list)

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
    records: list[dict] = field(default_factory=list)

    def line(self) -> str:
        return (
            f'{self.show}：搜索 {self.searched} 条，衍生 {self.derived_found} 条，'
            f'命中特别篇 {self.matched} 条，提交 {self.submitted}，跳过 {self.skipped}，失败 {self.failed}'
        )

    def add_record(self, candidate: Candidate, status: str, reason: str = '') -> None:
        """记录单条资源的去向，供插件页面逐条查看。"""
        self.records.append(
            {
                'show': self.show,
                'title': candidate.title,
                'site': candidate.site,
                'src': _source_label(candidate.parsed),
                'target': f'S00E{candidate.match.episode:02d}',
                'sxx_name': candidate.match.name,
                'score': round(candidate.match.score, 1),
                'size_gb': round(candidate.size_gb, 2),
                'seeders': candidate.seeders,
                'free': candidate.free,
                'status': status,
                'reason': reason,
            }
        )


def _source_label(parsed: ParsedTitle) -> str:
    """把站点编号、真实期号、衍生类型拼成可读标签。

    站点 SxxExx 只是发布序号，和节目期号常常不是一回事，必须分开显示，
    否则「S04E19」会被误读成「第4季第19期」，看不出问题出在哪。
    """
    parts: list[str] = []
    if parsed.site_season is not None and parsed.site_episode is not None:
        parts.append(f'站点S{parsed.site_season:02d}E{parsed.site_episode:02d}')
    elif parsed.site_episode is not None:
        parts.append(f'站点E{parsed.site_episode:02d}')
    if parsed.episode is not None and not parsed.episode_is_site_ordinal:
        season = f'S{parsed.season:02d}' if parsed.season is not None else ''
        parts.append(f'{season}第{parsed.episode}期')
    if parsed.subtype_word:
        parts.append(parsed.subtype_word)
    elif parsed.subtype:
        parts.append(parsed.subtype)
    if parsed.part:
        parts.append(f'{parsed.part}集')
    return ' '.join(parts) or '-'


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
    if candidate.parsed.is_pack:
        return '整季包，衍生内容不处理整季包'
    if candidate.seeders < options.min_seeders:
        return f'做种 {candidate.seeders} 少于阈值 {options.min_seeders}'
    size_gb = candidate.size_gb
    if size_gb and size_gb < options.min_size_gb:
        # 三位小数，避免 0.0499 被显示成「0.05 小于 0.05」这种自相矛盾的说法
        return f'体积 {size_gb:.3f}GB 小于下限 {options.min_size_gb:.3f}GB'
    if size_gb and size_gb > options.max_size_gb:
        return f'体积 {size_gb:.2f}GB 超过上限 {options.max_size_gb:.2f}GB'
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
        # 先解析一次，再一次性拿到该资源可落位的全部候选集号，
        # 落位环节需要在候选之间做取舍，只取最高分一条会导致多条资源撞同一集。
        parsed = parse_release(strip_show_name(title, names), strip_show_name(description, names))
        options = rank_specials(parsed, index)
        result = options[0] if options else None
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
                options=options,
            )
        )
    return candidates, derived, matched, unmatched


def _source_key(candidate: Candidate) -> tuple:
    """同一条资源的身份标识。

    优先按「季 + 类型 + 修饰词 + 分P + 片段名」认内容：不同站点的同一条资源、
    同一条资源的 1080p/2160p 两个版本，都指向同一份内容，必须落到同一个特别集号。
    没有片段名时才退回站点季集号。
    """
    parsed = candidate.parsed
    season = parsed.site_season if parsed.site_season is not None else parsed.season
    # 有真实期号时，期号本身就能唯一确定内容（同期同类型同修饰词同分P即同一段）
    if parsed.episode is not None and not parsed.episode_is_site_ordinal:
        return ('period', season, parsed.episode, parsed.subtype or '', parsed.modifier or '',
                parsed.part or '', parsed.segment or '')
    # 只有站点序号时，片段名是唯一可用的内容标识
    if parsed.segment and (parsed.subtype or parsed.modifier):
        return ('segment', season, parsed.subtype or '', parsed.modifier or '',
                parsed.part or '', parsed.segment.strip())
    episode = parsed.site_episode if parsed.site_episode is not None else parsed.episode
    return ('ordinal', season, episode, parsed.subtype or '', parsed.modifier or '', parsed.part or '')


def _source_order(candidate: Candidate) -> tuple:
    """按站点发布顺序排序：季号、集号升序，无编号的排最后。"""
    parsed = candidate.parsed
    season = parsed.site_season if parsed.site_season is not None else parsed.season
    episode = parsed.site_episode if parsed.site_episode is not None else parsed.episode
    return (
        1 if season is None else 0,
        season or 0,
        1 if episode is None else 0,
        episode or 0,
        candidate.parsed.segment or '',
        candidate.title,
    )


def assign_episodes(candidates: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """给每条资源分配互不相同的特别集号。

    TMDB 把同一期的正片、加更、多个纯享片段拆成多集（如 163/164/165 是同一期的
    三个纯享片段）。站点这边这些片段各占一个 SxxExx，但标题里的期号完全一样，
    单看一条资源分不出是哪一段。若各自独立取最高分，它们会全部撞到同一集。

    规则：
    1. 同一条资源（同站点编号）的多个版本归为一组，整组落到同一集；
    2. 组按站点发布顺序依次挑分最高、且尚未被占用的集号；
    3. 一个集号只给一组，宁可让多出来的资源「等待」，也不重复落位。
    """
    groups: dict[tuple, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(_source_key(candidate), []).append(candidate)

    ordered = sorted(groups.values(), key=lambda items: _source_order(items[0]))
    used: set[int] = set()
    chosen: list[Candidate] = []
    skipped: list[tuple[Candidate, str]] = []

    for items in ordered:
        pool: list[MatchResult] = []
        for item in items:
            options = item.options or [item.match]
            pool.extend(options)
        pool.sort(key=lambda option: (option.score, -option.episode), reverse=True)

        pick: Optional[MatchResult] = None
        seen: set[int] = set()
        for option in pool:
            if option.episode in seen:
                continue
            seen.add(option.episode)
            if option.episode in used:
                continue
            pick = option
            break

        if pick is None:
            reason = '可用的特别集号都已被本轮其他资源占用，本轮先不处理'
            for item in items:
                skipped.append((item, reason))
            continue

        used.add(pick.episode)
        for item in items:
            item.match = pick
            chosen.append(item)
    return chosen, skipped


def plan_tasks(
    candidates: list[Candidate],
    store: TaskStore,
    show: TrackedShow,
    options: Options,
) -> tuple[list[Candidate], list[tuple[Candidate, str]], dict[int, Candidate]]:
    """按特别集号分组择优，返回 (待下载, 跳过项及原因, 每个集号的胜出者)。"""
    grouped: dict[int, list[Candidate]] = {}
    skipped: list[tuple[Candidate, str]] = []

    for candidate in candidates:
        reason = filter_candidate(candidate, options)
        if reason:
            skipped.append((candidate, reason))
            continue
        grouped.setdefault(candidate.match.episode, []).append(candidate)

    chosen: list[Candidate] = []
    winners: dict[int, Candidate] = {}
    for episode, items in grouped.items():
        existing = store.get(show.tmdb_id, episode)
        if existing and existing.status in (STATUS_SUBMITTED, STATUS_QUEUED, 'done'):
            if not options.overwrite_existing:
                for item in items:
                    skipped.append(
                        (item, f'S00E{episode:02d} 本轮已有任务（{existing.status}），不重复下载')
                    )
                continue
        best = sorted(items, key=lambda item: rank_key(item, options.prefer_resolution))[0]
        chosen.append(best)
        winners[episode] = best
        for other in items:
            if other is not best:
                skipped.append(
                    (other, f'S00E{episode:02d} 同集多条资源，已选《{best.title}》')
                )
    return chosen, skipped, winners


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

    # 先按体积/做种筛掉不合规的资源，再做落位分配。
    # 顺序不能反：被筛掉的资源如果先占了集号，会让合规资源被迫挤到下一集。
    eligible: list[Candidate] = []
    pre_skipped: list[tuple[Candidate, str]] = []
    for candidate in candidates:
        reason = filter_candidate(candidate, options)
        if reason:
            pre_skipped.append((candidate, reason))
        else:
            eligible.append(candidate)

    # 多条资源撞同一集时按发布顺序重新分配，保证一条资源落一个集号
    candidates, assign_skipped = assign_episodes(eligible)

    # 扫到了衍生内容但 TMDB 特别季里没有登记：记录下来等下一轮，不静默丢弃
    head_lines: list[str] = []
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
        for title, parsed in unmatched:
            report.records.append(
                {
                    'show': show.name,
                    'title': title,
                    'site': '',
                    'src': _source_label(parsed),
                    'target': '-',
                    'sxx_name': '',
                    'score': 0.0,
                    'size_gb': 0.0,
                    'seeders': 0,
                    'free': False,
                    'status': '等待',
                    'reason': 'TMDB 特别季暂无对应集，已记录等待补齐',
                }
            )
        head_lines.append(f'{len(unmatched)} 条衍生内容在 TMDB 特别季中暂无对应集，已记录等待')

    chosen, skipped, _winners = plan_tasks(candidates, store, show, options)
    skipped.extend(assign_skipped)
    skipped.extend(pre_skipped)
    report.skipped = len(skipped)
    for candidate, reason in skipped:
        report.add_record(candidate, '跳过', reason)

    # 明细按「失败 → 已提交 → 跳过」排列：失败原因最需要被看见，
    # 通知里只截前若干行，排在跳过项后面就永远看不到。
    failed_lines: list[str] = []
    ok_lines: list[str] = []
    skip_lines = [f'跳过《{candidate.title}》：{reason}' for candidate, reason in skipped]

    for candidate in chosen:
        task = make_task(candidate, show)
        if dry_run:
            report.add_record(candidate, '预演', f'将落位 {task.target_label}')
            ok_lines.append(
                f'[预演] {task.target_label} ← 《{candidate.title}》'
                f'（{candidate.match.score:.1f} 分，{candidate.size_gb:.2f}GB，{candidate.seeders} 做种）'
            )
            continue
        store.upsert(task)
        if download_fn is None:
            report.add_record(candidate, '待提交', '未提供下载入口')
            continue
        try:
            ok, message = download_fn(candidate)
        except Exception as err:  # noqa: BLE001
            ok, message = False, str(err)
        if ok:
            store.update(task, status=STATUS_SUBMITTED, download_hash=message)
            report.submitted += 1
            report.add_record(candidate, '已提交', f'下载哈希 {message}')
            ok_lines.append(f'已提交 {task.target_label} ← 《{candidate.title}》')
        else:
            store.update(task, status=STATUS_SKIPPED, error=message)
            report.failed += 1
            reason = message or '下载接口未返回原因'
            report.add_record(candidate, '失败', reason)
            failed_lines.append(
                f'提交失败 {task.target_label}：《{candidate.title}》→ {reason}'
            )
            logger.warning(f'综艺衍生内容管家：提交失败《{candidate.title}》：{reason}')

    report.details = failed_lines + ok_lines + head_lines + skip_lines
    return report
