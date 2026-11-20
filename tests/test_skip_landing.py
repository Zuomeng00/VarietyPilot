"""「不下载的衍生类型」必须按落位结果挡，而不是只按资源名挡。

背景（用户实际遇到）：设置里关掉了「纯享」，站点这一条却还是下下来了。

站点给这一条的副标题是「第6期上·笨蛋杀手」，资源名是
`Natural High S04E11 2026 2160p ...`——两处都没有「纯享」二字。
插件解析出来的类型是 None（认不出），于是类型开关放行；等到落位时，
靠「期号一致 + 片段名一致」认准了 TMDB 的「第4季 第6期上纯享·笨蛋杀手」，
就把一条纯享下回来了。

用户关的是「纯享这类内容」，不是「资源名里带纯享这两个字的种子」。
所以落位时必须再按目标集自己的类型挡一次，其余类型照常。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])

INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    episodes=[
        SpecialEpisode(episode=160, name='第4季 第5期·白敬亭直拍', air_date='2026-11-09'),
        SpecialEpisode(episode=170, name='第4季 第5期加更', air_date='2026-11-09'),
        SpecialEpisode(episode=178, name='第4季 第6期上纯享·笨蛋杀手', air_date='2026-11-16'),
        SpecialEpisode(episode=181, name='第4季 第6期加更', air_date='2026-11-16'),
        SpecialEpisode(episode=182, name='第4季 第6期还有加更', air_date='2026-11-17'),
    ],
    season_years={4: 2026},
    search_names=['现在就出发', 'Natural High'],
    fetched_at=0,
)


class FakeTorrent:
    def __init__(self, title: str, description: str = '', size: int = 6 * 1024 ** 3,
                 seeders: int = 12, factor: float = 1.0, site: str = '观众') -> None:
        self.title = title
        self.description = description
        self.site_name = site
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = factor


class FakeContext:
    def __init__(self, title: str, description: str = '', **kwargs) -> None:
        self.torrent_info = FakeTorrent(title, description, **kwargs)


# 这两条是用户实际遇到的资源：站点只在副标题里写了期号和片段名
PURE_UNTYPED = FakeContext(
    'Natural High S04E11 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
    '现在就出发 第四季 第6期上·笨蛋杀手',
)
PLUS = FakeContext(
    'Natural High S04E11 Plus 2026 2160p TX WEB-DL H265 DDP2.0-AD',
    '现在就出发 第四季 第6期加更',
)
CLIP_UNTYPED = FakeContext(
    'Natural High S04E10 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
    '现在就出发 第四季 第5期·白敬亭',
)
PLUS_5 = FakeContext(
    'Natural High S04E09 Plus 2026 2160p TX WEB-DL H265 DDP2.0-AD',
    '现在就出发 第四季 第5期加更',
)


def _run(contexts: list, skip: list[str]):
    submitted: list[str] = []

    def download(candidate):
        submitted.append(candidate.title)
        return True, 'hash'

    tmp = tempfile.TemporaryDirectory()
    store = TaskStore(Path(tmp.name) / 'tasks.json')
    options = Options(
        min_seeders=1,
        min_size_gb=0.05,
        max_size_gb=20.0,
        prefer_resolution='2160p',
        skip_subtypes=skip,
    )
    report = scan_show(
        show=SHOW,
        index=INDEX,
        store=store,
        options=options,
        search_fn=lambda name, sites: list(contexts),
        download_fn=download,
    )
    store._tmp = tmp  # 保持临时目录存活到断言结束
    return submitted, report


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


def test_pure_blocked_by_landing() -> None:
    """资源名没写「纯享」，但落位目标是纯享集：关掉纯享就不该下。"""
    submitted, report = _run([PURE_UNTYPED], skip=['pure'])
    assert submitted == [], submitted
    assert report.submitted == 0, report.records
    assert any(
        item['status'] == '跳过'
        and item['target'] == 'S00E178'
        and '纯享' in item['reason']
        for item in report.records
    ), report.records
    assert any('按设置不下载' in line for line in report.details), report.details


def test_pure_still_downloaded_when_not_blocked() -> None:
    """没关纯享时照常下载，别把该下的也挡了。"""
    submitted, report = _run([PURE_UNTYPED], skip=[])
    assert submitted == [PURE_UNTYPED.torrent_info.title], submitted
    assert report.submitted == 1, report.records


def test_plus_not_affected() -> None:
    """同一轮里，关掉纯享只挡纯享，加更照常下。"""
    submitted, _report = _run([PURE_UNTYPED, PLUS], skip=['pure'])
    assert submitted == [PLUS.torrent_info.title], submitted


def test_clip_blocked_by_landing() -> None:
    """直拍同理：资源名只写期号和嘉宾名，落位到直拍集也要挡。"""
    submitted, report = _run([CLIP_UNTYPED, PLUS_5], skip=['clip'])
    assert submitted == [PLUS_5.torrent_info.title], submitted
    assert any(
        item['status'] == '跳过' and '直拍' in item['reason'] for item in report.records
    ), report.records


def test_summary_counts_landing_blocks() -> None:
    """被挡掉的条数要算进「已按设置跳过」，否则用户以为资源没搜到。"""
    _submitted, report = _run([PURE_UNTYPED, CLIP_UNTYPED], skip=['pure', 'clip'])
    lines = ' '.join(report.details)
    assert '按设置' in lines and '2 条' in lines, report.details


if __name__ == '__main__':
    check('纯享按落位目标挡下', test_pure_blocked_by_landing)
    check('没关的类型照常下', test_pure_still_downloaded_when_not_blocked)
    check('关纯享不影响加更', test_plus_not_affected)
    check('直拍同样按落位目标挡', test_clip_blocked_by_landing)
    check('跳过的条数进汇总', test_summary_counts_landing_blocks)
    print('全部通过：类型开关按落位目标生效')
