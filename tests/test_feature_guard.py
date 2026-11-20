"""副标题标了「正片」的资源，不能被当成衍生内容下到特别季。

背景（用户实际遇到，2.1.16 修）：设置里关掉「纯享」，任务列表却出现

    S00E178 第4季 第6期上纯享·笨蛋杀手 ← Natural High S04E11 2026 2160p ...

站点上这条 `S04E11` 的副标题是「现在就出发 第四季 正片 第06期上：笨蛋杀手2.0」，
是**正片**；真正的纯享是同一页的 `S04E24Pure` / `S04E11 Pure`（名字带 Pure、
副标题写纯享），那两条已经被类型开关挡掉了。

出错链路：正片也带季号 4、期号 6、分P 上、片段名「笨蛋杀手2.0」，
季号 2 分 + 期号 3 分 + 分P 1 分 + 片段名 4 分 = 10 分，
比「无衍生标记要 8.5 分」的门槛还高，于是正片被插件下走，
再改成 S00E178 塞进特别季——正片那一集反而缺了，媒体库里多出一条假的纯享。

所以「正片」必须当硬标记：副标题一旦写明正片，整条不进衍生链路。
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
from varietypilot.titles import parse_release, strip_show_name  # noqa: E402

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])

INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    episodes=[
        SpecialEpisode(episode=178, name='第4季 第6期上纯享·笨蛋杀手', air_date='2026-11-16'),
        SpecialEpisode(episode=181, name='第4季 第6期加更', air_date='2026-11-16'),
        SpecialEpisode(episode=182, name='第4季 第6期还有加更', air_date='2026-11-17'),
    ],
    season_years={4: 2026},
    search_names=['现在就出发', 'Natural High'],
    fetched_at=0,
)

NAMES = ['现在就出发', 'Natural High']

# 站点原样数据（截图里那一页）
FEATURE_2160 = (
    'Natural High S04E11 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
    '现在就出发 第四季 正片 第06期上：笨蛋杀手2.0，白敬亭“枪杀”老板',
)
FEATURE_1080 = (
    'Natural High S04E11 2026 1080p TX WEB-DL H265 AAC-ADWeb',
    '现在就出发 第四季 正片 第06期上：笨蛋杀手2.0，白敬亭“枪杀”老板',
)
PURE = (
    'Natural High S04E24Pure 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
    '现在就出发 第四季 纯享 第06期上纯享：笨蛋杀手：沈腾范丞丞卧底错过',
)
PLUS = (
    'Natural High S04E11 Plus 2026 2160p TX WEB-DL H265 DDP2.0-AD',
    '现在就出发 第四季 加更 第06期加更',
)
# 没写「正片」、也没写类型词的衍生资源（站点只给期号和片段名）
UNTYPED_DERIVED = (
    'Natural High S04E11 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
    '现在就出发 第四季 第06期上：笨蛋杀手',
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
    def __init__(self, pair: tuple[str, str], **kwargs) -> None:
        self.torrent_info = FakeTorrent(pair[0], pair[1], **kwargs)


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
    store._tmp = tmp
    return submitted, report


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


def test_parse_marks_feature() -> None:
    """副标题写正片时要留下 is_feature 标记，且不认成衍生类型。"""
    title, desc = FEATURE_2160
    parsed = parse_release(strip_show_name(title, NAMES), strip_show_name(desc, NAMES))
    assert parsed.is_feature, parsed.signals
    assert parsed.subtype is None, parsed.subtype
    pure_title, pure_desc = PURE
    pure = parse_release(
        strip_show_name(pure_title, NAMES), strip_show_name(pure_desc, NAMES)
    )
    assert not pure.is_feature and pure.subtype == 'pure', (pure.is_feature, pure.subtype)


def test_feature_not_downloaded() -> None:
    """全开类型（没关纯享）时，正片也不该被插件下走。"""
    submitted, report = _run([FakeContext(FEATURE_2160)], skip=[])
    assert submitted == [], submitted
    assert report.records == [], report.records
    assert any('正片' in line for line in report.details), report.details


def test_feature_1080p_also_blocked() -> None:
    """同一份正片的 1080p 版本也挡，别换个清晰度又漏进来。"""
    submitted, _report = _run([FakeContext(FEATURE_1080)], skip=[])
    assert submitted == [], submitted


def test_feature_does_not_block_other_types() -> None:
    """同一轮里正片、纯享、加更都有：只下加更。"""
    submitted, _report = _run(
        [FakeContext(FEATURE_2160), FakeContext(PURE), FakeContext(PLUS)],
        skip=['pure'],
    )
    assert submitted == [PLUS[0]], submitted


def test_untyped_derived_still_works() -> None:
    """没标正片、没写类型词的衍生资源照旧走落位，别一刀切。"""
    submitted, _report = _run([FakeContext(UNTYPED_DERIVED)], skip=[])
    assert submitted == [UNTYPED_DERIVED[0]], submitted


if __name__ == '__main__':
    check('正片被标记且不算类型', test_parse_marks_feature)
    check('正片不下（类型全开）', test_feature_not_downloaded)
    check('正片 1080p 同样挡', test_feature_1080p_also_blocked)
    check('挡正片不影响加更', test_feature_does_not_block_other_types)
    check('无类型衍生资源照旧落位', test_untyped_derived_still_works)
    print('全部通过：正片不进衍生链路')
