"""端到端流程测试：模拟站点搜索结果，验证分类、择优、任务生成与整理改写。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402


class FakeTorrent:
    def __init__(self, title: str, site: str = '站点A', size: int = 2 * 1024 ** 3, seeders: int = 10) -> None:
        self.title = title
        self.site_name = site
        self.size = size
        self.seeders = seeders


class FakeContext:
    def __init__(self, title: str, **kwargs) -> None:
        self.torrent_info = FakeTorrent(title, **kwargs)


JXCF_ROWS = [
    (53, '第3季 先导片上', '2025-10-18'),
    (57, '第3季 第1期加更', '2025-10-27'),
    (59, '第3季 第1期还有加更', '2025-10-28'),
    (64, '第3季 第2期加更', '2025-11-03'),
    (65, '第3季 第2期还有加更', '2025-11-04'),
]

INDEX = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    season_years={0: 2023, 1: 2023, 2: 2024, 3: 2025, 4: 2026},
)
INDEX.episodes = [SpecialEpisode(episode=e, name=n, air_date=d) for e, n, d in JXCF_ROWS]

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620)

SITE_RESULTS = [
    FakeContext('现在就出发.S03E01.第1期.2160p.WEB-DL', seeders=30),            # 正片，不该下
    FakeContext('现在就出发.S03E08.第1期.加更.1080p.WEB-DL', seeders=12),       # → S00E57
    FakeContext('现在就出发.S03E09.第1期.还有加更.1080p.WEB-DL', seeders=8),    # → S00E59
    FakeContext('现在就出发.S03E10.第2期.加更.1080p.WEB-DL', seeders=0),        # 无做种，跳过
    FakeContext('现在就出发.S03E11.第2期.加更.2160p.WEB-DL', seeders=50),       # → S00E64（与上条同期）
    FakeContext('现在就出发.S03.2025.2160p.WEB-DL', seeders=40),                # 整季包，跳过
    FakeContext('现在就出发.S03.第1期.彩蛋.1080p', seeders=9),                  # S3 无彩蛋登记，不匹配
]


def fake_search(name: str, sites: list[int]) -> list:
    return list(SITE_RESULTS)


def test_pipeline() -> list[str]:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        downloaded: list[str] = []

        def fake_download(candidate):
            downloaded.append(candidate.title)
            return True, f'hash-{len(downloaded)}'

        report = scan_show(
            show=SHOW,
            index=INDEX,
            store=store,
            options=Options(min_seeders=1),
            search_fn=fake_search,
            download_fn=fake_download,
            dry_run=False,
        )

        got = {task.sxx_episode for task in store.all()}
        if got != {57, 59, 64}:
            failures.append(f'落位集号期望 {{57, 59, 64}} 实际 {got}')
        if report.submitted != 3:
            failures.append(f'提交数期望 3 实际 {report.submitted}')
        if len(downloaded) != 3:
            failures.append(f'下载调用期望 3 次实际 {len(downloaded)}')

        # 第2期择优：选 2160p 高做种那条
        picked_64 = [t for t in store.all() if t.sxx_episode == 64]
        if not picked_64 or '2160p' not in picked_64[0].title:
            failures.append(f'S00E64 应选 2160p 候选，实际 {[t.title for t in picked_64]}')
        if picked_64 and picked_64[0].sxx_name != '第3季 第2期加更':
            failures.append(f'S00E64 集名错误：{picked_64[0].sxx_name}')

        # 重复扫描不应重复下载
        before = len(store.all())
        report2 = scan_show(
            show=SHOW,
            index=INDEX,
            store=store,
            options=Options(min_seeders=1),
            search_fn=fake_search,
            download_fn=fake_download,
            dry_run=False,
        )
        if len(store.all()) != before or report2.submitted != 0:
            failures.append(f'重复扫描产生了新任务：{len(store.all())} vs {before}，提交 {report2.submitted}')
    return failures


def test_rename_interception() -> list[str]:
    """验证整理事件能把一条加更改写到 S00。"""
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        scan_show(
            show=SHOW,
            index=INDEX,
            store=store,
            options=Options(min_seeders=1),
            search_fn=fake_search,
            download_fn=lambda candidate: (True, 'hash-x'),
            dry_run=False,
        )

        plugin = VarietyPilot()
        plugin._enabled = True
        plugin._store = store

        # MP 从文件名解析出 S03E08，任务里记录的是文本期号 1 与站点集号 8
        rename_dict = {
            'season': 3,
            'season_fmt': 'S03',
            'episode': 8,
            'season_episode': 'S03E08',
            'episode_title': '第1期',
            'name': '现在就出发',
            'original_name': '现在就出发.S03E08.第1期.加更.1080p.WEB-DL',
        }
        plugin.on_transfer_rename_build(SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict)))
        if rename_dict.get('season') != 0:
            failures.append(f'整理改写未生效，season={rename_dict.get("season")}')
        if rename_dict.get('season_episode') != 'S00E57':
            failures.append(f'季集号期望 S00E57 实际 {rename_dict.get("season_episode")}')

        # 渲染兜底
        data = SimpleNamespace(
            rename_dict=dict(rename_dict),
            render_str='现在就出发 (2023)/Season 03/现在就出发 - S03E08 - 第1期',
            updated=False,
            updated_str=None,
            source='',
        )
        plugin.on_transfer_rename(SimpleNamespace(event_data=data))
        if not data.updated or 'S00E57' not in (data.updated_str or ''):
            failures.append(f'渲染兜底未生效：updated={data.updated} str={data.updated_str}')

        # 无关文件不应被改写
        other = {
            'season': 3,
            'episode': 99,
            'name': '现在就出发',
            'original_name': '现在就出发.S03E99.第99期.2160p',
        }
        plugin.on_transfer_rename_build(SimpleNamespace(event_data=SimpleNamespace(rename_dict=other)))
        if other.get('season') != 3:
            failures.append('无关文件被误改写')
    return failures


def main() -> int:
    failures = test_pipeline() + test_rename_interception()
    if failures:
        print(f'失败 {len(failures)} 项：')
        for line in failures:
            print('  -', line)
        return 1
    print('全部通过：扫描链路 + 整理拦截')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
