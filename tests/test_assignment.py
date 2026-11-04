"""落位分配回归：同一期多条衍生资源不能挤到同一集。

数据取自 TMDB《现在就出发》第四季特别篇的真实集名（2026-11-04 现场抓取）。
站点把同一期的正片、加更、多个纯享片段各编一个 SxxExx，标题里的期号完全相同，
如果每条资源各自取最高分，它们会全部撞到同一集——这正是扫描日志里
「三条 Pure 全是 S00E159」的原因。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot.pipeline import (  # noqa: E402
    Options,
    TrackedShow,
    build_candidates,
    scan_show,
)
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402

# TMDB 真实集名（第 4 季部分，按集号升序）
JXCF_S4_ROWS = [
    (138, '第4季 第1期加更', '2026-10-12'),
    (139, '第4季 第1期还有加更', '2026-10-13'),
    (140, '第4季 第1期花絮特辑', '2026-10-15'),
    (141, '第4季 第2期上纯享·二指追击战', '2026-10-17'),
    (142, '第4季 第2期上纯享·牦牛接话王', '2026-10-17'),
    (143, '第2期下纯享·瞎掰故事王', '2026-10-18'),
    (144, '第2期下纯享·热舞的哞哞', '2026-10-18'),
    (145, '第4季 第2期加更', '2026-10-19'),
    (146, '第4季 第2期还有加更', '2026-10-20'),
    (147, '第4季 第2期花絮特辑', '2026-10-22'),
    (148, '第4季 第3期超前彩蛋', '2026-10-23'),
    (149, '第4季 第3期上纯享·偷题猜词', '2026-10-24'),
    (150, '第4季 第3期上纯享·笨蛋杀手', '2026-10-24'),
    (151, '第4季 直播回放', '2026-10-24'),
    (152, '第4季 第3期下纯享·你认了吗', '2026-10-25'),
    (153, '第4季 第3期下纯享·反向剧情传声筒', '2026-10-25'),
    (154, '第4季 第3期加更', '2026-10-26'),
    (155, '第4季 第3期还有加更', '2026-10-27'),
    (156, '第4季 直播回放', '2026-10-27'),
    (157, '第4季 第3期花絮特辑', '2026-10-29'),
    (158, '第4季 第4期超前彩蛋', '2026-10-30'),
    (159, '第4季 第4期上纯享·不眨眼唱歌', '2026-10-31'),
    (160, '第4季 第4期上纯享·即兴MV', '2026-10-31'),
    (161, '第4季 第4期上纯享·天黑吃香蕉', '2026-10-31'),
    (162, '第4季 直播回放', '2026-10-31'),
    (163, '第4季 第4期下纯享·遥控器猜词', '2026-11-01'),
    (164, '第4季 第4期下纯享·小画家', '2026-11-01'),
    (165, '第4季 第4期下纯享·拍卖猜歌', '2026-11-01'),
    (166, '第4季 第4期加更', '2026-11-02'),
    (167, '第4季 第4期还有加更', '2026-11-03'),
]

INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    episodes=[SpecialEpisode(episode=e, name=n, air_date=d) for e, n, d in JXCF_S4_ROWS],
    season_years={3: 2025, 4: 2026},
    search_names=['Natural High'],
    fetched_at=1.0,
)

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


def _scan(contexts: list, dry_run: bool = True):
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        report = scan_show(
            show=SHOW,
            index=INDEX,
            store=store,
            options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0),
            search_fn=lambda name, sites: list(contexts),
            download_fn=None if dry_run else (lambda c: (True, 'hash')),
            dry_run=dry_run,
        )
        return report


def _targets(report) -> dict[str, str]:
    """资源名 → 目标集号，只取有落位的记录。"""
    result = {}
    for record in report.records:
        if record.get('target') and record['target'] != '-':
            result[record['title']] = record['target']
    return result


def test_three_pure_segments_get_distinct_episodes() -> None:
    """三条纯享片段落位必须互相不同，不能全部挤到 159。"""
    contexts = [
        FakeContext('Natural High S04E17 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期上：不眨眼唱歌'),
        FakeContext('Natural High S04E18 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期上：即兴MV'),
        FakeContext('Natural High S04E19 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期上：天黑吃香蕉'),
    ]
    report = _scan(contexts)
    targets = _targets(report)
    assert len(targets) == 3, f'应有 3 条落位，实际 {targets}'
    assert len(set(targets.values())) == 3, f'三条纯享撞到同一集：{targets}'
    assert set(targets.values()) == {'S00E159', 'S00E160', 'S00E161'}, (
        f'上纯享应落在 159/160/161，实际 {targets}'
    )
    print(f'  Pure 分落位：{targets}')


def test_segment_name_pins_exact_episode() -> None:
    """副标题带片段名时，应直接命中同名的那一集。"""
    contexts = [
        FakeContext('Natural High S04E17 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：小画家'),
        FakeContext('Natural High S04E18 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：拍卖猜歌'),
    ]
    report = _scan(contexts)
    targets = _targets(report)
    assert targets.get('Natural High S04E17 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb') == 'S00E164', targets
    assert targets.get('Natural High S04E18 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb') == 'S00E165', targets
    print(f'  片段名精确命中：{targets}')


def test_plus_and_pure_do_not_collide() -> None:
    """日志现场：2 条加更 + 3 条纯享，五条资源必须是五个不同集号。"""
    contexts = [
        FakeContext('Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 加更 第04期加更：沈腾黄景瑜双簧帮唱'),
        FakeContext('Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 加更 第04期还有加更：白敬亭林一雷霆画功'),
        FakeContext('Natural High S04E17 Pure 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：遥控器猜词'),
        FakeContext('Natural High S04E18 Pure 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：小画家'),
        FakeContext('Natural High S04E19 Pure 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：拍卖猜歌'),
    ]
    report = _scan(contexts)
    targets = _targets(report)
    assert len(set(targets.values())) == len(targets), f'存在重复落位：{targets}'
    assert targets['Natural High S04E07 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb'] == 'S00E166', targets
    assert targets['Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb'] == 'S00E167', targets
    assert 'S00E159' not in set(targets.values()) - {targets[
        'Natural High S04E17 Pure 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb'
    ]} or True  # 159 只能出现一次
    assert list(targets.values()).count('S00E159') <= 1, f'159 被重复占用：{targets}'
    print(f'  加更 + 纯享分落位：{targets}')


def test_same_episode_two_resolutions_share_episode() -> None:
    """同一条资源的 1080p/2160p 是同一份内容，必须落到同一集。"""
    contexts = [
        FakeContext('Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                    '现在就出发 第四季 加更 第04期还有加更：白敬亭林一'),
        FakeContext('Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 加更 第04期还有加更：白敬亭林一'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        report = scan_show(
            show=SHOW,
            index=INDEX,
            store=store,
            options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0, prefer_resolution='1080p'),
            search_fn=lambda name, sites: list(contexts),
            download_fn=lambda candidate: (submitted.append(candidate.title) or (True, 'hash')),
        )
        assert report.submitted == 1, f'同一内容只应提交 1 条，实际 {report.submitted}'
        assert len(store.all()) == 1
        assert submitted[0].startswith('Natural High S04E08 Plus 2026 1080p'), submitted
    print('  同内容多清晰度归并到同一集，按偏好择一')


def test_unmatched_when_slots_full() -> None:
    """TMDB 只有 2 个纯享片段却有 3 条资源时，多出来的应等待而不是重复落位。"""
    index = SpecialIndex(
        tmdb_id=231620,
        show_name='现在就出发',
        episodes=[
            SpecialEpisode(episode=164, name='第4季 第4期下纯享·小画家', air_date='2026-11-01'),
            SpecialEpisode(episode=165, name='第4季 第4期下纯享·拍卖猜歌', air_date='2026-11-01'),
        ],
        season_years={4: 2026},
        search_names=['Natural High'],
        fetched_at=1.0,
    )
    contexts = [
        FakeContext('Natural High S04E17 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：遥控器猜词'),
        FakeContext('Natural High S04E18 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：小画家'),
        FakeContext('Natural High S04E19 Pure 2026 1080p TX WEB-DL H265 AAC-ADWeb',
                    '现在就出发 第四季 纯享 第04期下：拍卖猜歌'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        report = scan_show(
            show=SHOW,
            index=index,
            store=store,
            options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0),
            search_fn=lambda name, sites: list(contexts),
            download_fn=lambda candidate: (True, 'hash'),
            dry_run=True,
        )
        episodes = [task for task in report.records if task['status'] == '预演']
        assert len(episodes) == 2, f'只该落 2 条，实际 {len(episodes)}'
        targets = {record['target'] for record in episodes}
        assert len(targets) == 2, f'两条预演撞到同一集：{targets}'
    print(f'  集号不足时只落 {len(episodes)} 条，其余等待')


def main() -> int:
    test_three_pure_segments_get_distinct_episodes()
    test_segment_name_pins_exact_episode()
    test_plus_and_pure_do_not_collide()
    test_same_episode_two_resolutions_share_episode()
    test_unmatched_when_slots_full()
    print('全部通过：落位分配')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
