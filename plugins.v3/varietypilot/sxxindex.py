"""TMDB 特别季（Season 0）索引与匹配。

核心思路：TMDB 的 S00 集名本身就是「第N季 第M期加更」这样的结构化中文标题，
所以用同一套解析器把它拆成字段，再用字段比对代替字符串模糊匹配。
这样既准又可解释，出错时能直接说出是哪一条字段不匹配。

匹配不依赖任何人工维护的映射表，剧集和季度变化时自动跟随 TMDB。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .titles import ParsedTitle, detect_subtype, parse_release, parse_title, strip_show_name

logger = logging.getLogger(__name__)

# 匹配打分权重
SCORE_SEASON_HIT = 2.0
SCORE_SEASON_YEAR_HIT = 1.5
SCORE_YEAR_HIT = 2.0
SCORE_YEAR_MISS = 3.0
SCORE_EPISODE_HIT = 3.0
SCORE_SUBTYPE_HIT = 3.0
SCORE_SUBTYPE_NEAR = 1.0
SCORE_MODIFIER_HIT = 2.0
SCORE_MODIFIER_MISS = 1.5
SCORE_PART_HIT = 1.0
SCORE_SEGMENT_HIT = 4.0
SCORE_SEGMENT_NEAR = 2.0
SCORE_DATE_EXACT = 2.0
SCORE_DATE_NEAR = 1.5
SCORE_DATE_FAR = 1.0
MIN_ACCEPT_SCORE = 6.0

# 类型相近关系：命中同一个「族」时给部分分
SUBTYPE_NEIGHBORS: dict[str, set[str]] = {
    'plus_bonus': {'bonus', 'pure'},
    'bonus': {'plus_bonus', 'pure'},
    'pure': {'plus_bonus', 'bonus'},
    'special': {'extra'},
    'extra': {'special', 'live'},
    'live': {'extra'},
    'clip': {'extra'},
    'prequel': {'special'},
}


@dataclass(slots=True)
class SpecialEpisode:
    """TMDB S00 中的一集。"""

    episode: int
    name: str
    air_date: Optional[str] = None
    tmdb_episode_id: Optional[int] = None

    # 由集名反向解析出的结构化信号
    season: Optional[int] = None
    period: Optional[int] = None      # 集名里写的「第X期」
    subtype: Optional[str] = None
    modifier: Optional[str] = None
    part: Optional[str] = None
    year: Optional[int] = None
    segment: Optional[str] = None     # 集名「·」后的片段名，如「小画家」

    def __post_init__(self) -> None:
        parsed = parse_title(self.name)
        self.season = parsed.season
        self.period = parsed.episode
        self.subtype = parsed.subtype
        self.modifier = parsed.modifier
        self.part = parsed.part
        self.year = parsed.year
        self.segment = parsed.segment


@dataclass(slots=True)
class MatchResult:
    """匹配结果，带可解释的打分细节。"""

    episode: int
    name: str
    score: float
    reasons: list[str] = field(default_factory=list)
    air_date: Optional[str] = None

    def describe(self) -> str:
        return f'S00E{self.episode:02d} 《{self.name}》 得分 {self.score:.1f}（{"; ".join(self.reasons)}）'


@dataclass
class SpecialIndex:
    """某部剧的特别季索引。"""

    tmdb_id: int
    show_name: str = ''
    episodes: list[SpecialEpisode] = field(default_factory=list)
    season_years: dict[int, int] = field(default_factory=dict)
    # TMDB 上的中文名与原名。站点资源名常用英文原名（Natural High），
    # 所以这两个都要参与搜索和剧名剥离，否则中文名搜不到任何资源。
    search_names: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    def year_of_season(self, season: int) -> Optional[int]:
        return self.season_years.get(season)

    def season_of_year(self, year: int) -> Optional[int]:
        """按年份反查季号；同一年多季时取最接近的。"""
        candidates = [season for season, value in self.season_years.items() if value == year and season > 0]
        return min(candidates) if candidates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            'tmdb_id': self.tmdb_id,
            'show_name': self.show_name,
            'episodes': [asdict(item) for item in self.episodes],
            'season_years': {str(k): v for k, v in self.season_years.items()},
            'search_names': list(self.search_names),
            'fetched_at': self.fetched_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SpecialIndex':
        episodes = []
        for item in data.get('episodes') or []:
            # 反序列化时避免重复解析
            episode = SpecialEpisode(
                episode=int(item.get('episode') or 0),
                name=str(item.get('name') or ''),
                air_date=item.get('air_date') or None,
                tmdb_episode_id=item.get('tmdb_episode_id'),
            )
            episode.season = item.get('season')
            episode.period = item.get('period')
            episode.subtype = item.get('subtype')
            episode.modifier = item.get('modifier')
            episode.part = item.get('part')
            episode.year = item.get('year')
            episode.segment = item.get('segment')
            episodes.append(episode)
        return cls(
            tmdb_id=int(data.get('tmdb_id') or 0),
            show_name=str(data.get('show_name') or ''),
            episodes=episodes,
            season_years={int(k): int(v) for k, v in (data.get('season_years') or {}).items()},
            search_names=[str(item) for item in (data.get('search_names') or []) if item],
            fetched_at=float(data.get('fetched_at') or 0),
        )


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        try:
            result = value.to_dict()
            if isinstance(result, dict):
                return result
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, '__dict__'):
        return dict(vars(value))
    return {}


def _parse_date(text: Any) -> Optional[date]:
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(str(text)[:10], fmt).date()
        except ValueError:
            continue
    return None


# 优先取英语区的标题：PT 站点的资源名基本沿用 TMDB 英文标题
_PREFERRED_ALT_REGIONS = ('US', 'GB', 'CN', 'HK', 'TW')


def _collect_names(details: dict, fallback: str = '') -> list[str]:
    """收集中文名与原名，用于站点搜索和剧名剥离。"""
    names: list[str] = []
    for key in ('name', 'original_name'):
        value = details.get(key)
        if value:
            names.append(str(value).strip())
    if fallback:
        names.append(fallback.strip())
    return _dedupe(names)


def _collect_alt_names(alt: dict) -> list[str]:
    """从 alternative_titles 里取英语区标题，按偏好区排序。"""
    results = alt.get('results') or alt.get('titles') or []
    buckets: dict[str, list[str]] = {}
    for item in results:
        info = _as_dict(item)
        title = str(info.get('title') or '').strip()
        region = str(info.get('iso_3166_1') or '').upper()
        if title:
            buckets.setdefault(region, []).append(title)
    ordered: list[str] = []
    for region in _PREFERRED_ALT_REGIONS:
        ordered.extend(buckets.pop(region, []))
    for region in sorted(buckets):
        ordered.extend(buckets[region])
    return _dedupe(ordered)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


class SpecialIndexStore:
    """拉取并缓存特别季索引。"""

    def __init__(
        self,
        cache_dir: Path,
        ttl_seconds: int = 12 * 3600,
        client: Any = None,
        client_factory: Any = None,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._ttl = max(300, int(ttl_seconds))
        self._client = client
        self._client_factory = client_factory

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        from app.modules.themoviedb.tmdbapi import TmdbApi

        self._client = TmdbApi()
        return self._client

    def _cache_file(self, tmdb_id: int) -> Path:
        return self._cache_dir / f'sxx_{tmdb_id}.json'

    def load(self, tmdb_id: int, show_name: str = '', force: bool = False) -> SpecialIndex:
        """读取索引；缓存过期或强制刷新时回源 TMDB。"""
        cached = self._read_cache(tmdb_id)
        if cached and not force and time.time() - cached.fetched_at < self._ttl:
            if show_name and not cached.show_name:
                cached.show_name = show_name
            return cached
        try:
            fresh = self._fetch(tmdb_id, show_name)
        except Exception as err:  # noqa: BLE001
            logger.warning(f'综艺衍生季索引：TMDB 拉取失败（{tmdb_id}）：{err}')
            if cached:
                logger.info(f'综艺衍生季索引：TMDB 不可用，沿用过期缓存（{tmdb_id}）')
                return cached
            raise
        self._write_cache(fresh)
        return fresh

    def _read_cache(self, tmdb_id: int) -> Optional[SpecialIndex]:
        path = self._cache_file(tmdb_id)
        if not path.exists():
            return None
        try:
            return SpecialIndex.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError) as err:
            logger.debug(f'综艺衍生季索引：缓存损坏已丢弃 {path.name}：{err}')
            return None

    def _write_cache(self, index: SpecialIndex) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_file(index.tmdb_id).write_text(
                json.dumps(index.to_dict(), ensure_ascii=False), encoding='utf-8'
            )
        except OSError as err:
            logger.debug(f'综艺衍生季索引：缓存写入失败：{err}')

    def _fetch(self, tmdb_id: int, show_name: str) -> SpecialIndex:
        client = self._client_or_create()
        details = _as_dict(client.tv.details(tmdb_id))
        if not details.get('id'):
            raise ValueError('TMDB 未返回剧集信息')

        season_years: dict[int, int] = {}
        has_special = False
        for item in details.get('seasons') or []:
            info = _as_dict(item)
            try:
                number = int(info.get('season_number'))
            except (TypeError, ValueError):
                continue
            if number == 0:
                has_special = True
            air = _parse_date(info.get('air_date'))
            if air:
                season_years[number] = air.year

        search_names = _collect_names(details, show_name)
        # 站点资源名常用英文标题（Natural High / Up All Friday Night），
        # 只靠中文名会一条都搜不到，所以要把别名也取回来。
        try:
            alt = _as_dict(client.tv.alternative_titles(tmdb_id))
            for name in _collect_alt_names(alt):
                if name not in search_names:
                    search_names.append(name)
        except Exception as err:  # noqa: BLE001
            logger.debug(f'综艺衍生季索引：别名获取失败（{tmdb_id}）：{err}')

        index = SpecialIndex(
            tmdb_id=tmdb_id,
            show_name=str(details.get('name') or show_name or ''),
            season_years=season_years,
            search_names=search_names[:4],
            fetched_at=time.time(),
        )
        if not has_special:
            logger.info(f'综艺衍生季索引：{index.show_name} 在 TMDB 没有特别季')
            return index

        season = _as_dict(client.season_obj.details(tmdb_id, 0))
        for item in season.get('episodes') or []:
            info = _as_dict(item)
            try:
                number = int(info.get('episode_number'))
            except (TypeError, ValueError):
                continue
            index.episodes.append(
                SpecialEpisode(
                    episode=number,
                    name=str(info.get('name') or '').strip(),
                    air_date=str(info.get('air_date') or '')[:10] or None,
                    tmdb_episode_id=info.get('id'),
                )
            )
        index.episodes.sort(key=lambda ep: ep.episode)

        # 集名里写了年份、但季表里没有该年份时，尝试用集名补 season_years
        for episode in index.episodes:
            if episode.year and episode.season and episode.season not in season_years:
                season_years[episode.season] = episode.year
        return index


def _date_gap(left: Optional[date], right: Optional[date]) -> Optional[int]:
    if not left or not right:
        return None
    return abs((left - right).days)


_PUNCT_RE = re.compile(r'[\s·・,，。.、:：;；!！?？\-_/\\|()（）\[\]【】"“”\'‘’]+')


def _norm_segment(text: Optional[str]) -> str:
    """归一化片段名：去标点空格，只留可用于比对的字符。"""
    if not text:
        return ''
    return _PUNCT_RE.sub('', text).lower()


def _segment_score(left: Optional[str], right: Optional[str]) -> tuple[float, str]:
    """比较两条片段名，返回 (分数, 说明)。

    站点副标题常被截断（末尾省略号），所以用包含关系判断，
    再退到二元组重合度，避免「小画家」和「小画师」被误判成同一段。
    """
    a, b = _norm_segment(left), _norm_segment(right)
    if len(a) < 2 or len(b) < 2:
        return 0.0, ''
    if a == b:
        return SCORE_SEGMENT_HIT, f'片段名一致（{right}）'
    if a in b or b in a:
        return SCORE_SEGMENT_HIT, f'片段名吻合（{right} 含 {left}）'
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a or not grams_b:
        return 0.0, ''
    overlap = len(grams_a & grams_b) / len(grams_a | grams_b)
    if overlap >= 0.5:
        return SCORE_SEGMENT_NEAR, f'片段名相似 {overlap:.0%}'
    return -SCORE_SEGMENT_NEAR, f'片段名不同（{left}≠{right}）'


def score_episode(
    target: ParsedTitle,
    candidate: SpecialEpisode,
    index: SpecialIndex,
    target_date: Optional[date] = None,
) -> MatchResult:
    """对单条候选打分。打分维度全部来自标题字段，可回溯。"""
    score = 0.0
    reasons: list[str] = []

    # 季维度
    if target.season is not None and candidate.season is not None:
        if target.season == candidate.season:
            score += SCORE_SEASON_HIT
            reasons.append(f'季一致 S{target.season:02d}')
        else:
            score -= SCORE_SEASON_HIT
            reasons.append(f'季不符 S{target.season:02d}≠S{candidate.season:02d}')
    elif target.year is not None and candidate.season is not None:
        mapped = index.year_of_season(candidate.season)
        if mapped == target.year:
            score += SCORE_SEASON_YEAR_HIT
            reasons.append(f'年份映射一致 {target.year}→S{candidate.season:02d}')
    elif target.season is not None and candidate.year is not None:
        mapped_season = index.season_of_year(candidate.year)
        if mapped_season == target.season:
            score += SCORE_SEASON_YEAR_HIT
            reasons.append(f'季一致（按年份 {candidate.year}）')

    # 年份维度：S00 里常用「2026 加更版第1期」表示季，年份不同必然不是同一季
    if target.year and candidate.year:
        if target.year == candidate.year:
            score += SCORE_YEAR_HIT
            reasons.append(f'年份一致 {target.year}')
        else:
            score -= SCORE_YEAR_MISS
            reasons.append(f'年份不符 {target.year}≠{candidate.year}')

    # 期号维度：只认「真实期号」。站点 SxxExx 往往只是发布序号
    # （同一期的正片、加更、纯享片段各占一个 E 号），拿去比 TMDB 的「第X期」必然错位。
    if target.episode is not None and candidate.period is not None and not target.episode_is_site_ordinal:
        if target.episode == candidate.period:
            score += SCORE_EPISODE_HIT
            reasons.append(f'期号一致 第{target.episode}期')
        else:
            return MatchResult(episode=candidate.episode, name=candidate.name, score=-99.0, reasons=['期号不符'])
    elif target.episode_is_site_ordinal and candidate.period is not None:
        reasons.append(f'站点序号 E{target.episode}（不作期号）')

    # 类型维度
    if target.subtype and candidate.subtype:
        if target.subtype == candidate.subtype:
            score += SCORE_SUBTYPE_HIT
            reasons.append(f'类型一致 {target.subtype}')
        elif candidate.subtype in SUBTYPE_NEIGHBORS.get(target.subtype, set()):
            score += SCORE_SUBTYPE_NEAR
            reasons.append(f'类型相近 {target.subtype}≈{candidate.subtype}')
        else:
            score -= SCORE_SUBTYPE_HIT
            reasons.append(f'类型不符 {target.subtype}≠{candidate.subtype}')
    elif target.subtype and not candidate.subtype:
        reasons.append('候选无类型标记')

    # 修饰词：区分「第1期加更」与「第1期还有加更」这类同期多条记录
    if target.modifier and candidate.modifier:
        if target.modifier == candidate.modifier:
            score += SCORE_MODIFIER_HIT
            reasons.append(f'修饰词一致（{target.modifier}）')
        else:
            score -= SCORE_MODIFIER_HIT
            reasons.append(f'修饰词不符（{target.modifier}≠{candidate.modifier}）')
    elif target.modifier and not candidate.modifier:
        score -= SCORE_MODIFIER_MISS
        reasons.append('候选项缺少修饰词')
    elif candidate.modifier and not target.modifier:
        score -= SCORE_MODIFIER_MISS
        reasons.append(f'候选带修饰词（{candidate.modifier}）')

    # 分P
    if target.part and candidate.part:
        if target.part == candidate.part:
            score += SCORE_PART_HIT
            reasons.append(f'分P一致（{target.part}）')
        else:
            score -= SCORE_PART_HIT
            reasons.append(f'分P不符（{target.part}≠{candidate.part}）')
    elif target.part and not candidate.part:
        score -= SCORE_PART_HIT
        reasons.append('候选项未标分P')

    # 片段名：同一期拆成多个纯享片段时，只有这一层能区分
    if target.segment and candidate.segment:
        delta, note = _segment_score(target.segment, candidate.segment)
        if delta or note:
            score += delta
            reasons.append(note)

    # 日期兜底：越接近分数越高
    gap = _date_gap(target_date or target.air_date, _parse_date(candidate.air_date))
    if gap is not None:
        if gap == 0:
            score += SCORE_DATE_EXACT
            reasons.append('日期一致')
        elif gap <= 2:
            score += SCORE_DATE_NEAR
            reasons.append(f'日期接近（差{gap}天）')
        elif gap <= 4:
            score += SCORE_DATE_FAR
            reasons.append(f'日期较远（差{gap}天）')

    return MatchResult(
        episode=candidate.episode,
        name=candidate.name,
        score=score,
        reasons=reasons,
        air_date=candidate.air_date,
    )


def rank_specials(
    target: ParsedTitle,
    index: SpecialIndex,
    air_date: Optional[date] = None,
    limit: int = 8,
) -> list[MatchResult]:
    """返回按分数降序的候选集列表，供落位环节挑一个没被占用的集号。

    只返回达到最低门槛的结果；分数相近的若干条都会保留，
    因为同一期往往有多个片段集，单看一条资源的信号分不出是哪一段。
    """
    if not index.episodes:
        return []
    scored = [score_episode(target, episode, index, target_date=air_date) for episode in index.episodes]
    scored = [item for item in scored if item.score > -50]
    if not scored:
        return []
    scored.sort(key=lambda item: (item.score, -item.episode), reverse=True)

    best_score = scored[0].score
    floor = MIN_ACCEPT_SCORE
    # 期号不可用（站点序号）或标题信息很少时，信号本就少，门槛相应放宽，
    # 剩下的交给「一条资源落一个集号」的顺序分配去纠偏。
    if target.episode is None or target.episode_is_site_ordinal:
        floor -= 1.0
    # 保留与最佳结果相差不超过 3 分的候选：同一期的多个片段通常就落在这一档里
    keep = [item for item in scored if item.score >= max(floor, best_score - 3.0)]
    return keep[:limit]


def match_special(
    target: ParsedTitle,
    index: SpecialIndex,
    air_date: Optional[date] = None,
) -> Optional[MatchResult]:
    """在特别季里找出与发布标题最匹配的一集。

    :param target: 已剥离剧名的发布标题解析结果
    :param index: 该剧的特别季索引
    :param air_date: 调用方已知的播出日期，用于补充打分
    :return: 最高分且达到阈值的匹配；无合适结果时返回 None
    """
    ranked = rank_specials(target, index, air_date=air_date, limit=1)
    if not ranked:
        logger.debug(f'综艺衍生季匹配：{target.raw!r} 没有达到门槛的候选')
        return None
    return ranked[0]


def match_for_title(
    raw_title: str,
    show_names: list[str],
    index: SpecialIndex,
    air_date: Optional[date] = None,
    description: str = '',
) -> tuple[Optional[MatchResult], ParsedTitle]:
    """从原始发布标题出发完成匹配。

    先剥离剧名再解析，避免剧名里的年份/期号污染信号。
    资源名提供站点编号，副标题提供中文期号与衍生类型。
    """
    cleaned_title = strip_show_name(raw_title, show_names)
    cleaned_desc = strip_show_name(description, show_names) if description else ''
    parsed = parse_release(cleaned_title, cleaned_desc)
    return match_special(parsed, index, air_date=air_date), parsed


def detect_derived_without_keyword(
    raw_title: str,
    show_names: list[str],
    index: SpecialIndex,
) -> Optional[MatchResult]:
    """标题没有类型词时，判断它是否其实是一条特别篇。

    用于「尝鲜篇」「森林体验篇」这类 TMDB 有登记、但站点名不带类型词的情况。
    只接受期号与日期同时吻合的高分结果。
    """
    cleaned = strip_show_name(raw_title, show_names)
    parsed = parse_title(cleaned)
    if parsed.subtype is not None or parsed.episode is None:
        return None
    result = match_special(parsed, index)
    if result and result.score >= MIN_ACCEPT_SCORE + 1:
        return result
    return None


def next_free_episode(index: SpecialIndex, reserved: set[int] | None = None) -> int:
    """返回 S00 尾部可用集号，用于兜底追加。"""
    used = {episode.episode for episode in index.episodes}
    if reserved:
        used |= reserved
    return (max(used) + 1) if used else 1


def date_window(day: date, days: int = 2) -> tuple[date, date]:
    return day - timedelta(days=days), day + timedelta(days=days)
