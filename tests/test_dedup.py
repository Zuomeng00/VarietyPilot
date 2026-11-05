"""重复下载与订阅同步节流测试。

两个真实现场：
1. 站点同一条内容同时有 1080p 和 2160p，插件把两个版本各下了一遍；
2. TMDB 特别季补录后集号整体后移，同一份内容换了集号又被当成新内容下一遍。

判断依据必须是「内容指纹」，不能是集号。
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
    content_key,
    build_candidates,
    scan_show,
)
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'])


class FakeTorrent:
    def __init__(self, title: str, description: str = '', size: int = 2 * 1024 ** 3,
                 seeders: int = 10, factor: float = 1.0) -> None:
        self.title = title
        self.description = description
        self.site_name = '站点A'
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = factor


class FakeContext:
    def __init__(self, title: str, description: str = '', **kwargs) -> None:
        self.torrent_info = FakeTorrent(title, description, **kwargs)


def _index(rows: list[tuple[int, str]], season_years: dict | None = None) -> SpecialIndex:
    return SpecialIndex(
        tmdb_id=231620,
        show_name='现在就出发',
        episodes=[SpecialEpisode(episode=e, name=n) for e, n in rows],
        season_years=season_years or {4: 2026},
        search_names=['Natural High'],
        fetched_at=time.time(),
    )


INDEX_BASE = _index(
    [
        (140, '第4季 第1期花絮特辑'),
        (145, '第4季 第2期加更'),
        (166, '第4季 第4期加更'),
        (167, '第4季 第4期还有加更'),
        (168, '第4季 第4期纯享·小画家'),
    ]
)

# TMDB 补录后集号整体后移一位
INDEX_SHIFTED = _index(
    [
        (140, '第4季 第1期花絮特辑'),
        (145, '第4季 第2期加更'),
        (167, '第4季 第4期加更'),
        (168, '第4季 第4期还有加更'),
        (169, '第4季 第4期纯享·小画家'),
    ]
)

OPTIONS = Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0, prefer_resolution='2160p')


def _run(store: TaskStore, index: SpecialIndex, contexts: list, submitted: list[str]):
    def download(candidate):
        submitted.append(candidate.title)
        return True, 'hash'

    return scan_show(
        show=SHOW,
        index=index,
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


def test_two_resolutions_same_round() -> None:
    """同一轮里 1080p 与 2160p 是同一份内容，只提交一条。"""
    contexts = [
        FakeContext('Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱'),
        FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        report = _run(store, INDEX_BASE, contexts, submitted)
        assert report.submitted == 1, f'只应提交 1 条，实际 {report.submitted}：{submitted}'
        assert submitted[0].find('2160p') > 0, f'偏好 2160p，实际选了 {submitted[0]}'
        skip_lines = [r['reason'] for r in report.records if r['status'] == '跳过']
        assert any('1080p' in line and '2160p' in line for line in skip_lines), skip_lines


def test_resolution_switch_not_downloaded_again() -> None:
    """上一轮下了 1080p，这一轮站点补了 2160p，不能当成新内容再下一次。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        first: list[str] = []
        _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            first,
        )
        assert len(first) == 1, first

        second: list[str] = []
        report = _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            second,
        )
        assert second == [], f'同一内容换清晰度不该再下：{second}'
        assert report.submitted == 0
        reasons = [r['reason'] for r in report.records if r['status'] == '跳过']
        assert any('已下载过' in line for line in reasons), reasons
        assert any('S00E166' in line for line in reasons), reasons


def test_episode_shift_does_not_redownload() -> None:
    """TMDB 补录导致集号后移，同一份内容仍然不能重复下载。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        first: list[str] = []
        _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            first,
        )
        assert len(first) == 1, first

        second: list[str] = []
        _run(
            store,
            INDEX_SHIFTED,
            [FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            second,
        )
        assert second == [], f'集号后移不该触发重复下载：{second}'


def test_new_content_still_downloaded() -> None:
    """去重只挡同一份内容，同期的「还有加更」照常下载。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        first: list[str] = []
        _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            first,
        )
        second: list[str] = []
        report = _run(
            store,
            INDEX_BASE,
            [
                FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                            '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱'),
                FakeContext('Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                            '现在就出发 第四季 加更 第04期还有加更：白敬亭林一'),
            ],
            second,
        )
        assert len(second) == 1, f'应只下载新内容，实际 {second}'
        assert 'S04E08' in second[0], second
        assert report.submitted == 1


def test_content_key_ignores_resolution() -> None:
    """指纹必须与清晰度无关，否则换版本就绕过去了。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        contexts = [
            FakeContext('Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                        '现在就出发 第四季 加更 第04期加更'),
            FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                        '现在就出发 第四季 加更 第04期加更'),
        ]
        candidates, _d, _m, _u = build_candidates(contexts, SHOW, INDEX_BASE)
        keys = {content_key(item) for item in candidates}
        assert len(candidates) == 2, len(candidates)
        assert len(keys) == 1, f'两个清晰度应共用一个指纹：{keys}'


def test_resolution_beats_free() -> None:
    """偏好 2160p 时，免费 1080p 不能把它挤掉。

    旧版把「免费」排在清晰度前面：第一轮选了免费的 1080p，
    等 2160p 变成免费又下一遍，同一份内容就成了两个版本。
    """
    contexts = [
        FakeContext('Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 加更 第04期加更', size=2 * 1024 ** 3, factor=0.0),
        FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 加更 第04期加更', size=5 * 1024 ** 3, factor=1.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        _run(store, INDEX_BASE, contexts, submitted)
        assert len(submitted) == 1, submitted
        assert '2160p' in submitted[0], f'应选 2160p，实际 {submitted[0]}'


def test_legacy_task_without_fingerprint() -> None:
    """升级前的任务没有内容指纹，只能按站点序号判重，同样不能重复下载。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        first: list[str] = []
        _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            first,
        )
        assert len(first) == 1, first
        for task in store.all():
            store.update(task, content_key='')  # 模拟 2.1.2 留下的旧记录

        second: list[str] = []
        report = _run(
            store,
            INDEX_BASE,
            [FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                         '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱')],
            second,
        )
        assert second == [], f'旧记录也要挡住重复下载：{second}'
        reasons = [r['reason'] for r in report.records if r['status'] == '跳过']
        assert any('已下载过' in line for line in reasons), reasons


def test_sync_interval_gate() -> None:
    """订阅同步按间隔节流：默认 30 分钟，0 表示每轮都同步。"""
    plugin = VarietyPilot()
    plugin._config = {'sync_interval_minutes': 30}
    plugin._sync_at = time.time()
    assert plugin._sync_due() is False, '刚同步过不该再同步'

    plugin._sync_at = time.time() - 31 * 60
    assert plugin._sync_due() is True, '超过 30 分钟应同步'

    plugin._config = {'sync_interval_minutes': 0}
    plugin._sync_at = time.time()
    assert plugin._sync_due() is True, '间隔为 0 时应每轮同步'

    plugin._config = {'sync_interval_minutes': 120}
    plugin._sync_at = time.time() - 60 * 60
    assert plugin._sync_due() is False, '自定义 120 分钟时 60 分钟内不该同步'


def test_sync_interval_default() -> None:
    plugin = VarietyPilot()
    plugin._config = {}
    assert plugin._sync_interval_minutes() == 30, plugin._sync_interval_minutes()


if __name__ == '__main__':
    check('同一轮两个清晰度只提交一条', test_two_resolutions_same_round)
    check('换清晰度不再重复下载', test_resolution_switch_not_downloaded_again)
    check('集号后移不再重复下载', test_episode_shift_does_not_redownload)
    check('新内容照常下载', test_new_content_still_downloaded)
    check('指纹与清晰度无关', test_content_key_ignores_resolution)
    check('清晰度优先于免费', test_resolution_beats_free)
    check('升级前的旧记录也能挡住重复', test_legacy_task_without_fingerprint)
    check('同步间隔节流', test_sync_interval_gate)
    check('同步间隔默认 30 分钟', test_sync_interval_default)
    print('全部通过：重复下载拦截 + 同步节流')
