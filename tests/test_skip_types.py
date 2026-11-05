"""「不下载的衍生类型」开关测试。

背景：直拍是单人机位素材，站点文件名里只有 Fancam，没有嘉宾名，
插件只能按发布时间猜是哪一位，落错人的概率很高。与其下错，
不如整类关掉。

顺带把国内平台的花式命名补齐——「会员Plus版」「悠享版」「超长花絮」
「母带放送」这些词原来根本认不出来，认不出就等于开关对它无效。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402
from varietypilot.titles import parse_title  # noqa: E402


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
        SpecialEpisode(episode=57, name='第3季 第1期加更'),
        SpecialEpisode(episode=60, name='第3季 白敬亭直拍'),
        SpecialEpisode(episode=63, name='第3季 黄景瑜直拍'),
        SpecialEpisode(episode=70, name='第3季 彩蛋'),
        SpecialEpisode(episode=83, name='第3季 第5期加更'),
    ],
    season_years={3: 2025},
    search_names=['Natural High'],
    fetched_at=0,
)

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[3])


def _options(skip: list[str]) -> Options:
    return Options(
        min_seeders=1,
        min_size_gb=0.05,
        max_size_gb=20.0,
        prefer_resolution='1080p',
        skip_subtypes=skip,
    )


def _run(contexts: list, skip: list[str]) -> tuple[list[str], object, TaskStore]:
    submitted: list[str] = []

    def download(candidate):
        submitted.append(candidate.title)
        return True, 'hash'

    tmp = tempfile.TemporaryDirectory()
    store = TaskStore(Path(tmp.name) / 'tasks.json')
    report = scan_show(
        show=SHOW,
        index=INDEX,
        store=store,
        options=_options(skip),
        search_fn=lambda name, sites: list(contexts),
        download_fn=download,
    )
    store._tmp = tmp  # 保持临时目录存活到断言结束
    return submitted, report, store


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


# ------------------------------------------------------------ 命名识别

NAMING_CASES: list[tuple[str, str]] = [
    # 加更 / 会员版家族
    ('现在就出发 会员Plus版 第3期 1080p', 'plus_bonus'),
    ('现在就出发 悠享版 第3期 1080p', 'plus_bonus'),
    ('现在就出发 加长版 第3期 1080p', 'plus_bonus'),
    ('现在就出发 VIP加长版 第3期 1080p', 'plus_bonus'),
    ('现在就出发 会员加更 第3期 1080p', 'plus_bonus'),
    ('现在就出发 特别加更 第3期 1080p', 'plus_bonus'),
    ('Natural.High.S03E25.Plus.2025.1080p.WEB-DL', 'plus_bonus'),
    # 纯享
    ('现在就出发 单人纯享 第3期 1080p', 'pure'),
    ('现在就出发 纯享版 第3期 1080p', 'pure'),
    # 彩蛋 / 花絮 必须分得开
    ('现在就出发 VIP彩蛋 第3期 1080p', 'bonus'),
    ('现在就出发 番外篇 第3期 1080p', 'bonus'),
    ('现在就出发 超长花絮 第3期 1080p', 'uncut'),
    ('现在就出发 未播片段 第3期 1080p', 'uncut'),
    ('现在就出发 母带放送 第3期 1080p', 'uncut'),
    ('现在就出发 NG片段 第3期 1080p', 'uncut'),
    ('现在就出发 幕后 第3期 1080p', 'uncut'),
    # 直拍：中文名和站点常用英文写法都要认
    ('现在就出发 精彩直拍 第5期 1080p', 'clip'),
    ('现在就出发 第一视角 第5期 1080p', 'clip'),
    ('Natural.High.S03E28.Fancam.2025.1080p.WEB-DL', 'clip'),
    # 预热
    ('现在就出发 抢先看 第3期 1080p', 'prequel'),
    ('现在就出发 下期预告 第3期 1080p', 'prequel'),
    ('现在就出发 先导片 1080p', 'prequel'),
    # 特辑 / 直播 / 独立衍生
    ('现在就出发 特别企划 第3期 1080p', 'special'),
    ('现在就出发 游戏特辑 第3期 1080p', 'special'),
    ('现在就出发 陪看 第3期 1080p', 'live'),
    ('现在就出发 开播直播夜 1080p', 'live'),
    ('现在就出发 超前营业 第2期 1080p', 'extra'),
    ('现在就出发 圆桌 第2期 1080p', 'extra'),
]


def test_naming() -> None:
    """国内综艺的常见命名都要落到同一套类型上。"""
    wrong = []
    for title, expect in NAMING_CASES:
        got = parse_title(title).subtype
        if got != expect:
            wrong.append(f'{title} → {got}（应为 {expect}）')
    assert not wrong, '；'.join(wrong)


def test_fancam_is_clip() -> None:
    """Fancam 是站点标直拍的写法，认不出就会被当成无类型资源随便落位。"""
    parsed = parse_title('Natural.High.S03E28.Fancam.2025.1080p.WEB-DL.H265.AAC-ADWeb')
    assert parsed.subtype == 'clip', parsed.subtype


# ------------------------------------------------------------ 开关行为

CLIP = '现在就出发 直拍.Natural.High.S03E28.Fancam.2025.1080p.WEB-DL'
PLUS = '现在就出发 加更.Natural.High.S03E25.Plus.2025.1080p.WEB-DL'
UNCUT = '现在就出发 花絮.Natural.High.S03E27.2025.1080p.WEB-DL'
BONUS = '现在就出发 彩蛋.Natural.High.S03E29.2025.1080p.WEB-DL'


def test_clip_skipped() -> None:
    """关掉直拍后，直拍一条都不下，其他类型照常。"""
    submitted, report, _store = _run(
        [FakeContext(CLIP), FakeContext(PLUS)], skip=['clip']
    )
    assert submitted == [PLUS], submitted
    assert report.submitted == 1, report.submitted
    assert any('跳过' in line and '直拍' in line for line in report.details), report.details


def test_clip_kept_when_cleared() -> None:
    """清空选择表示全都要，直拍照常下。"""
    submitted, _report, _store = _run([FakeContext(CLIP)], skip=[])
    assert len(submitted) == 1, submitted


def test_uncut_skipped_without_touching_bonus() -> None:
    """花絮和彩蛋是两类：关花絮不影响彩蛋。"""
    submitted, _report, _store = _run(
        [FakeContext(UNCUT), FakeContext(BONUS)], skip=['uncut']
    )
    assert submitted == [BONUS], submitted


def test_multiple_types_skipped() -> None:
    """可以同时关掉多类。"""
    submitted, _report, _store = _run(
        [FakeContext(CLIP), FakeContext(UNCUT), FakeContext(PLUS)],
        skip=['clip', 'uncut'],
    )
    assert submitted == [PLUS], submitted


def test_report_mentions_skipped_count() -> None:
    """通知里要说清挡了几条，否则用户以为资源没搜到。"""
    _submitted, report, _store = _run(
        [FakeContext(CLIP), FakeContext(CLIP.replace('E28', 'E31')), FakeContext(PLUS)],
        skip=['clip'],
    )
    lines = ' '.join(report.details)
    assert '直拍' in lines and '2 条' in lines, report.details


# ------------------------------------------------------------ 配置解析

def test_parse_subtypes() -> None:
    """配置值认类型键、中文标签、逗号串、数组四种写法。"""
    parse = VarietyPilot._parse_subtypes
    assert parse(['clip', '直拍']) == ['clip'], parse(['clip', '直拍'])
    assert parse('clip,uncut') == ['clip', 'uncut'], parse('clip,uncut')
    assert parse('直拍，花絮') == ['clip', 'uncut'], parse('直拍，花絮')
    assert parse(None) == [], parse(None)
    assert parse('不存在的类型') == [], parse('不存在的类型')


def test_default_skips_clip() -> None:
    """默认配置就关掉直拍：装完即可生效，不用再进设置点一遍。"""
    from varietypilot import DEFAULT_CONFIG

    assert DEFAULT_CONFIG['skip_subtypes'] == ['clip'], DEFAULT_CONFIG['skip_subtypes']


if __name__ == '__main__':
    check('常见命名都能识别', test_naming)
    check('Fancam 识别为直拍', test_fancam_is_clip)
    check('关掉直拍后直拍不下', test_clip_skipped)
    check('清空后直拍照常下', test_clip_kept_when_cleared)
    check('关花絮不影响彩蛋', test_uncut_skipped_without_touching_bonus)
    check('可同时关掉多类', test_multiple_types_skipped)
    check('通知里写明跳过的条数', test_report_mentions_skipped_count)
    check('配置值四种写法都认', test_parse_subtypes)
    check('默认关掉直拍', test_default_skips_clip)
    print('全部通过：类型开关 + 命名覆盖')
