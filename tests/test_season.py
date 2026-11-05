"""订阅季范围测试：只下订阅那一季的衍生内容。

真实现场：订阅「现在就出发 第四季」，站点同时有第 1~4 季的加更，
插件把前几季的加更也搜下来一并提交，媒体库里混进一堆追不到的旧季。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import (  # noqa: E402
    Options,
    TrackedShow,
    build_candidates,
    scan_show,
)
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex, rank_specials  # noqa: E402
from varietypilot.titles import parse_release  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402

OPTIONS = Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0, prefer_resolution='1080p')


class FakeTorrent:
    def __init__(self, title: str, description: str = '', size: int = 2 * 1024 ** 3,
                 seeders: int = 10, factor: float = 1.0, site: str = '站点A') -> None:
        self.title = title
        self.description = description
        self.site_name = site
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = factor


class FakeContext:
    def __init__(self, title: str, description: str = '', **kwargs) -> None:
        self.torrent_info = FakeTorrent(title, description, **kwargs)


INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    episodes=[
        SpecialEpisode(episode=120, name='第3季 第5期加更'),
        SpecialEpisode(episode=163, name='第4季 第4期纯享·小画家'),
        SpecialEpisode(episode=166, name='第4季 第4期加更'),
        SpecialEpisode(episode=167, name='第4季 第4期还有加更'),
    ],
    season_years={3: 2024, 4: 2026},
    search_names=['Natural High'],
    fetched_at=time.time(),
)

CTX_OLD = FakeContext(
    'Natural High S03E05 Plus 2024 1080p TX WEB-DL H265 AAC-ADWeb',
    '现在就出发 第三季 加更 第05期加更',
)
CTX_NEW = FakeContext(
    'Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
    '现在就出发 第四季 加更 第04期加更',
)


def _run(show: TrackedShow, contexts: list, submitted: list[str], store: TaskStore):
    def download(candidate):
        submitted.append(candidate.title)
        return True, 'hash'

    return scan_show(
        show=show,
        index=INDEX,
        store=store,
        options=OPTIONS,
        search_fn=lambda name, sites: list(contexts),
        download_fn=download,
    )


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


def test_only_subscribed_season() -> None:
    """订阅第四季时，第三季的加更不下载，第四季的照常下。"""
    show = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        report = _run(show, [CTX_OLD, CTX_NEW], submitted, store)
        assert len(submitted) == 1, f'只该下第四季，实际 {submitted}'
        assert 'S04E07' in submitted[0], submitted
        assert any('S00E166' in line for line in report.details), report.details
        assert any('已排除其他季' in line for line in report.details), report.details


def test_no_season_limit_downloads_all() -> None:
    """没配季号（整部剧订阅）时不受限制，两季都下。"""
    show = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'])
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        _run(show, [CTX_OLD, CTX_NEW], submitted, store)
        assert len(submitted) == 2, f'不限季时应各下一份：{submitted}'


def test_rank_specials_skips_other_season() -> None:
    """候选集也要按季过滤，避免第 4 季的资源落到第 3 季的集号上。"""
    parsed = parse_release('Natural High S04E07 Plus 1080p', '现在就出发 第四季 加更 第04期加更')
    limited = rank_specials(parsed, INDEX, seasons=[4])
    assert limited, '第四季应有候选'
    assert all(item.episode >= 160 for item in limited), [item.episode for item in limited]

    free = rank_specials(parsed, INDEX, seasons=[])
    assert free, free
    assert {166} <= {item.episode for item in free}


def test_build_candidates_reports_filtered() -> None:
    """被季过滤掉的资源要能被记下来，日志里说清楚为什么没下。"""
    show = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])
    filtered: list[str] = []
    candidates, _derived, _matched, _unmatched = build_candidates(
        [CTX_OLD, CTX_NEW], show, INDEX, season_filtered=filtered
    )
    assert len(candidates) == 1, [item.title for item in candidates]
    assert len(filtered) == 1, filtered
    assert 'S03E05' in filtered[0], filtered


def test_year_style_season_not_filtered() -> None:
    """站点用 2026E07 这种年份写法时，不能当成「第 2026 季」拦掉。"""
    show = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])
    ctx = FakeContext(
        'Natural High 2026E07 Plus 1080p TX WEB-DL H265 AAC-ADWeb',
        '现在就出发 第四季 加更 第04期加更',
    )
    filtered: list[str] = []
    candidates, _d, _m, _u = build_candidates([ctx], show, INDEX, season_filtered=filtered)
    assert len(candidates) == 1, f'年份写法的季号不该被拦：{filtered}'
    assert not filtered, filtered


def test_parse_shows_season_field() -> None:
    """手填剧列表支持第五段季号。"""
    shows = VarietyPilot._parse_shows('现在就出发|231620|2023||4\n我家那闺女|126490', '')
    assert shows[0].seasons == [4], shows[0].seasons
    assert shows[1].seasons == [], shows[1].seasons


def test_parse_seasons_variants() -> None:
    """季号写法兼容 4 / S4 / 第4季 / 4,5。"""
    parse = VarietyPilot._parse_seasons
    assert parse('4') == [4]
    assert parse('S4') == [4]
    assert parse('第4季') == [4]
    assert parse('4,5') == [4, 5]
    assert parse('4，5') == [4, 5]
    assert parse('') == []
    assert parse('0') == []
    assert parse(None) == []
    assert parse([4, '5']) == [4, 5]


def test_season_label() -> None:
    assert TrackedShow(name='x', tmdb_id=1).season_label == '全部季'
    assert TrackedShow(name='x', tmdb_id=1, seasons=[4]).season_label == '第4季'
    assert TrackedShow(name='x', tmdb_id=1, seasons=[3, 4]).season_label == '第3、4季'


def test_stale_cache_forces_sync() -> None:
    """升级前的订阅缓存没有季号，必须强制重新同步，不能按「不限季」跑一轮。"""
    import json

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'auto_shows.json'
        path.write_text(
            json.dumps(
                {'at': time.time(), 'shows': [{'tmdb_id': 231620, 'name': '现在就出发', 'year': '2023'}]},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        plugin = VarietyPilot()
        plugin._config = {'sync_subscribe': True}
        plugin._data_path = Path(tmp)
        plugin._load_auto_shows()
        assert plugin._sync_at == 0.0, '旧缓存应强制同步'
        assert plugin._sync_due() is True
        assert plugin._auto_shows and plugin._auto_shows[0].seasons == []


def test_cache_with_seasons_kept() -> None:
    """带季号的新缓存正常恢复，不打乱同步节流。"""
    import json

    stamp = time.time() - 60
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'auto_shows.json'
        path.write_text(
            json.dumps(
                {
                    'at': stamp,
                    'shows': [
                        {'tmdb_id': 231620, 'name': '现在就出发', 'year': '2023', 'seasons': [4]}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        plugin = VarietyPilot()
        plugin._config = {'sync_subscribe': True}
        plugin._data_path = Path(tmp)
        plugin._load_auto_shows()
        assert plugin._auto_shows[0].seasons == [4]
        assert plugin._sync_due() is False, '60 秒前刚同步过，不该再同步'


def test_collect_subscription_seasons() -> None:
    """从订阅里读出季号；同一部剧订了整部时取消季限制。"""
    from varietypilot.subscribes import collect

    base = {'media_source': 'themoviedb', 'media_id': 231620, 'name': '现在就出发', 'type': '电视剧'}
    shows, _ = collect([dict(base, season=4), dict(base, season=5)], is_variety=lambda _: True)
    assert shows[0].seasons == [4, 5], shows[0].seasons

    shows, _ = collect(
        [dict(base, season=4), dict(base, season=0)], is_variety=lambda _: True
    )
    assert shows[0].seasons == [], f'整部剧订阅应取消季限制：{shows[0].seasons}'


if __name__ == '__main__':
    check('只下订阅那一季', test_only_subscribed_season)
    check('不限季时全下', test_no_season_limit_downloads_all)
    check('候选集按季过滤', test_rank_specials_skips_other_season)
    check('过滤结果可上报', test_build_candidates_reports_filtered)
    check('年份写法不误杀', test_year_style_season_not_filtered)
    check('手填列表支持季号', test_parse_shows_season_field)
    check('季号写法兼容', test_parse_seasons_variants)
    check('季范围文案', test_season_label)
    check('旧缓存强制重新同步', test_stale_cache_forces_sync)
    check('带季号的缓存正常恢复', test_cache_with_seasons_kept)
    check('订阅季号采集', test_collect_subscription_seasons)
    print('全部通过：订阅季范围')
