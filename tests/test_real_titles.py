"""真实 PT 资源名回归。

样本取自 PT 站点实际发布：资源名是「英文剧名 + SxxExx + 压制参数」，
中文期号与衍生类型在站点副标题里。这组用例覆盖两类关键行为：

1. 加更/彩蛋类能按真实期号落到正确的 TMDB 特别集；
2. 正片（含正片标记）绝不进入插件链路，更不能被改到 Season 00。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import Options, TrackedShow, build_candidates, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex, match_for_title  # noqa: E402
from varietypilot.store import STATUS_SUBMITTED, Task, TaskStore, VERIFY_UNKNOWN  # noqa: E402
from varietypilot.titles import parse_release  # noqa: E402


class FakeTorrent:
    def __init__(self, title: str, description: str = '', site: str = 'PT站点',
                 size: int = 3 * 1024 ** 3, seeders: int = 12,
                 downloadvolumefactor: float = 1.0) -> None:
        self.title = title
        self.description = description
        self.site_name = site
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = downloadvolumefactor


class FakeContext:
    def __init__(self, title: str, description: str = '', **kwargs) -> None:
        self.torrent_info = FakeTorrent(title, description, **kwargs)


# 《现在就出发》特别季：S3 结束在第 108 集，S4 的特别篇顺延在后。
# 集号取自 TMDB 的真实结构，具体数字以现场数据为准，匹配靠字段而非编号。
JXCF_S00 = [
    (57, '第3季 第1期加更', '2025-10-27'),
    (59, '第3季 第1期还有加更', '2025-10-28'),
    (105, '第3季 第12期加更', '2026-01-05'),
    (109, '第4季 第1期加更', '2026-08-02'),
    (110, '第4季 第2期加更', '2026-08-09'),
    (111, '第4季 第3期加更', '2026-08-16'),
    (112, '第4季 第4期加更', '2026-08-23'),
    (113, '第4季 第4期还有加更', '2026-08-24'),
]

# 《不眠的星期五》：超前营夜单独编号
WMXQ_S00 = [
    (1, '第1季 超前营夜 第1期', '2026-07-04'),
    (2, '第1季 超前营夜 第2期', '2026-07-11'),
    (3, '第1季 抢先看 第1期', '2026-06-27'),
]

JXCF_INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    episodes=[SpecialEpisode(episode=n, name=name, air_date=date) for n, name, date in JXCF_S00],
    season_years={3: 2025, 4: 2026},
    search_names=['Natural High'],
    fetched_at=1.0,
)

WMXQ_INDEX = SpecialIndex(
    tmdb_id=999001,
    show_name='不眠的星期五',
    episodes=[SpecialEpisode(episode=n, name=name, air_date=date) for n, name, date in WMXQ_S00],
    season_years={1: 2026},
    search_names=['Up All Friday Night'],
    fetched_at=1.0,
)

SHOW_JXCF = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'])
SHOW_WMXQ = TrackedShow(name='不眠的星期五', tmdb_id=999001, aliases=['Up All Friday Night'])

CASES = [
    (
        '第四季 第04期还有加更',
        'Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
        '现在就出发 第四季 加更 第04期还有加更：白敬亭林一雷霆画功惊呆沈腾 | 现在就出发4',
        JXCF_INDEX,
        SHOW_JXCF,
        '第4季 第4期还有加更',
    ),
    (
        '第四季 第04期加更',
        'Natural High S04E07 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
        '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱黄冰王楚然 | 现在就出发4',
        JXCF_INDEX,
        SHOW_JXCF,
        '第4季 第4期加更',
    ),
    (
        '第三季 第01期还有加更',
        'Natural High S03E09 Plus 2025 1080p TX WEB-DL H265 AAC-ADWeb',
        '现在就出发 第三季 加更 第01期还有加更：某期内容',
        JXCF_INDEX,
        SHOW_JXCF,
        '第3季 第1期还有加更',
    ),
    (
        '超前营夜 第1期',
        'Up All Friday Night S01E19 Friend Night 2026 2160p WEB-DL H265 AAC-ADWeb',
        '不眠的星期五 第一季 超前营夜 超前营夜“第1期”：李小冉唐艺昕在线提供唱郭京飞秒变吃瓜现场',
        WMXQ_INDEX,
        SHOW_WMXQ,
        '第1季 超前营夜 第1期',
    ),
]


def test_parse() -> None:
    cases = [
        ('Natural High S04E08 Plus 2026 2160p', '现在就出发 第四季 加更 第04期还有加更',
         dict(season=4, episode=4, subtype='plus_bonus', modifier='again', site_episode=8)),
        ('Natural High S04E06 2026 2160p', '现在就出发 第四季 正片 第05期',
         dict(season=4, episode=5, subtype=None, site_episode=6)),
        ('Mao Xue Woof S01E02 Special 2021 1080p', '毛雪汪 第一季 特别 五周年祝福惊喜掉落',
         dict(season=1, episode=None, subtype='special', site_episode=2)),
    ]
    for title, desc, expected in cases:
        parsed = parse_release(title, desc)
        for key, value in expected.items():
            actual = getattr(parsed, key)
            assert actual == value, f'{title} 的 {key} 应为 {value}，实际 {actual}'
    print(f'解析通过：{len(cases)} 组真实资源名')


def test_match() -> None:
    for label, title, desc, index, show, expected_name in CASES:
        result, parsed = match_for_title(title, show.search_names, index, description=desc)
        assert result is not None, f'{label} 未匹配到任何特别集'
        assert result.name == expected_name, f'{label} 匹配到 {result.name!r}，应为 {expected_name!r}'
        print(f'  {label} → S00E{result.episode:02d} 《{result.name}》（{result.score:.1f} 分）')
    print(f'匹配通过：{len(CASES)} 条真实资源名')


def test_feature_rejected() -> None:
    """正片不能被当成衍生内容。"""
    contexts = [
        FakeContext(
            'Natural High S04E06 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            '现在就出发 第四季 正片 第05期：本期内容',
        ),
        FakeContext(
            'Love Wins All S02E05 2026 1080p YK WEB-DL H264 AAC-ADWeb',
            '优选之恋 第二季 正片 第03期 情侣大片现场甜蜜暴击',
        ),
    ]
    candidates, derived, matched, _ = build_candidates(contexts, SHOW_JXCF, JXCF_INDEX)
    assert derived == 0, f'正片不应被识别为衍生内容，实际 {derived} 条'
    assert not candidates, f'正片不应进入候选，实际 {len(candidates)} 条'
    print('正片拦截通过：0 条进入候选')


def test_feature_not_rewritten() -> None:
    """即使正片与加更同季同集号，整理阶段也不得改写。"""
    plugin = VarietyPilot()
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        # 加更任务：站点 S04E08 → S00E113
        store.upsert(Task(
            tmdb_id=231620, show_name='现在就出发', sxx_episode=113,
            sxx_name='第4季 第4期还有加更',
            title='Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            src_season=4, src_episode=4, src_site_season=4, src_site_episode=8,
            subtype='plus_bonus', subtype_word='还有加更',
            status=STATUS_SUBMITTED, verify=VERIFY_UNKNOWN,
        ))
        plugin._store = store

        # 同站点编号的正片：绝不能命中
        feature = {
            'name': '现在就出发',
            'original_name': 'Natural High S04E08 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            'season': 4,
            'episode': 8,
        }
        assert plugin._match_task(feature) is None, '正片被误判为衍生内容'

        # 同一集的加更：应当命中
        bonus = {
            'name': '现在就出发',
            'original_name': 'Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            'season': 4,
            'episode': 8,
        }
        task = plugin._match_task(bonus)
        assert task is not None and task.sxx_episode == 113, '加更未被正确匹配'
    print('整理改写门禁通过：正片不改，加更正常')


def test_scan_end_to_end() -> None:
    """扫描链路：真实资源名 → 下载提交 → 任务记录。"""
    contexts = [
        FakeContext(
            'Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            '现在就出发 第四季 加更 第04期还有加更：白敬亭林一',
            size=5 * 1024 ** 3, seeders=20, downloadvolumefactor=0.0,
        ),
        FakeContext(
            'Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
            '现在就出发 第四季 加更 第04期还有加更：白敬亭林一',
            size=2 * 1024 ** 3, seeders=30, downloadvolumefactor=1.0,
        ),
        FakeContext(
            'Natural High S04E06 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
            '现在就出发 第四季 正片 第05期：本期内容',
        ),
    ]
    submitted: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        report = scan_show(
            show=SHOW_JXCF,
            index=JXCF_INDEX,
            store=store,
            options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0),
            search_fn=lambda name, sites: contexts if name in ('Natural High', '现在就出发') else [],
            download_fn=lambda candidate: (submitted.append(candidate.title) or (True, 'hash123')),
        )
        assert report.submitted == 1, f'应只提交 1 条，实际 {report.submitted}'
        assert submitted[0].endswith('2160p TX WEB-DL H265 DDP2.0-ADWeb'), '应优先选择免费种'
        task = store.get(231620, 113)
        assert task is not None, '任务未落库'
        assert task.src_site_episode == 8 and task.src_episode == 4, '站点编号与真实期号未分别记录'
        assert task.download_hash == 'hash123'
    print('扫描链路通过：免费优先、正片排除、站点编号与期号分别记录')


if __name__ == '__main__':
    test_parse()
    test_match()
    test_feature_rejected()
    test_feature_not_rewritten()
    test_scan_end_to_end()
    print('全部通过：真实 PT 资源名回归')
