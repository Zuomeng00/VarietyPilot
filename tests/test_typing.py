"""落位类型约束测试：加更、直拍、彩蛋各归各的集。

真实现场（2026-11-05 的整理记录）：
- 「加更.S03E25.Plus」被落到 S00E63「第3季 黄景瑜直拍」上；
- 「加更.S03E23.Plus」和「直拍.S03E23.Fancam」都落到 S00E83，
  后一个文件把前一个覆盖了。

两处根因：落位只比分数、不比类型；整理时按站点编号回连任务，
同一个 E23 下面挂着两条任务就随手挑一条。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.store import STATUS_SUBMITTED, Task, TaskStore  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402

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
        SpecialEpisode(episode=57, name='第3季 第1期加更'),
        SpecialEpisode(episode=60, name='第3季 白敬亭直拍'),
        SpecialEpisode(episode=63, name='第3季 黄景瑜直拍'),
        SpecialEpisode(episode=83, name='第3季 第5期加更'),
        SpecialEpisode(episode=84, name='第3季 第5期还有加更'),
    ],
    season_years={3: 2025},
    search_names=['Natural High'],
    fetched_at=time.time(),
)

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[3])


def _run(contexts: list, submitted: list[str], store: TaskStore, dry_run: bool = False):
    def download(candidate):
        submitted.append(candidate.title)
        return True, 'hash'

    return scan_show(
        show=SHOW,
        index=INDEX,
        store=store,
        options=OPTIONS,
        search_fn=lambda name, sites: list(contexts),
        download_fn=download,
        dry_run=dry_run,
    )


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


def test_plus_not_into_clip() -> None:
    """加更不能落到直拍集上。"""
    ctx = FakeContext(
        '现在就出发 加更.Natural.High.S03E25.Plus.2025.1080p.WEB-DL.H265.AAC-ADWeb'
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        report = _run([ctx], submitted, store)
        assert len(submitted) == 1, submitted
        task = store.all()[0]
        assert task.sxx_episode in (57, 83, 84), f'加更应落加更集，实际 {task.sxx_name}'
        assert '直拍' not in task.sxx_name, task.sxx_name
        assert report.submitted == 1


def test_clip_not_into_plus() -> None:
    """直拍也不能落到加更集上。"""
    ctx = FakeContext(
        '现在就出发 直拍.Natural.High.S03E28.Fancam.2025.1080p.WEB-DL.H265.AAC-ADWeb'
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        _run([ctx], submitted, store)
        task = store.all()[0]
        assert task.sxx_episode in (60, 63), f'直拍应落直拍集，实际 {task.sxx_name}'
        assert '加更' not in task.sxx_name, task.sxx_name


def test_same_site_number_two_types() -> None:
    """同一个站点编号下的加更和直拍，各落各的集，不能挤到同一集。"""
    contexts = [
        FakeContext('现在就出发 加更.Natural.High.S03E23.Plus.2025.2160p.WEB-DL'),
        FakeContext('现在就出发 直拍.Natural.High.S03E23.Fancam.2025.1080p.WEB-DL'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        _run(contexts, submitted, store)
        assert len(submitted) == 2, f'两条不同内容都要下：{submitted}'
        episodes = {task.sxx_episode for task in store.all()}
        assert len(episodes) == 2, f'不能落到同一集：{episodes}'
        names = {task.sxx_name for task in store.all()}
        assert any('加更' in name for name in names), names
        assert any('直拍' in name for name in names), names


def test_unknown_type_waits_instead_of_stealing() -> None:
    """TMDB 没登记的类型（第 3 季没彩蛋）不占加更的集，宁可等待。

    2.1.9 起原因文案从「TMDB 暂无对应集」改成「没有类型相符的特别集」：
    TMDB 这一季是有衍生集的，只是没有彩蛋这一类，原来的说法会让人以为
    TMDB 漏登记了这一集，白等。
    """
    ctx = FakeContext('现在就出发 彩蛋.Natural.High.S03E30.2025.1080p.WEB-DL')
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        submitted: list[str] = []
        report = _run([ctx], submitted, store)
        assert submitted == [], f'彩蛋不该占加更集：{submitted}'
        reasons = [r['reason'] for r in report.records]
        assert any('类型相符' in line or '等待' in line for line in reasons), reasons


def test_rename_matches_by_signature() -> None:
    """整理时同一个站点编号挂着两条任务，按内容指纹回连，不能随手挑。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        plus = Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=83,
            sxx_name='第3季 第5期加更',
            title='现在就出发 加更.Natural.High.S03E23.Plus.2025.2160p.WEB-DL',
            src_season=3,
            src_episode=23,
            src_site_season=3,
            src_site_episode=23,
            subtype='plus_bonus',
            subtype_word='加更',
            content_key='sig|3|23||plus_bonus||',
            status=STATUS_SUBMITTED,
        )
        clip = Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=60,
            sxx_name='第3季 白敬亭直拍',
            title='现在就出发 直拍.Natural.High.S03E23.Fancam.2025.1080p.WEB-DL',
            src_season=3,
            src_episode=23,
            src_site_season=3,
            src_site_episode=23,
            subtype='clip',
            subtype_word='直拍',
            content_key='sig|3|23||clip||',
            status=STATUS_SUBMITTED,
        )
        store.upsert(plus)
        store.upsert(clip)

        plugin = VarietyPilot()
        plugin._config = {'enabled': True}
        plugin._enabled = True
        plugin._store = store

        rename_dict = {
            'season': 3,
            'season_fmt': 'S03',
            'episode': 23,
            'season_episode': 'S03E23',
            'episode_title': '第5期加更',
            'name': '现在就出发',
            'original_name': '现在就出发 加更.Natural.High.S03E23.Plus.2025.2160p.WEB-DL',
        }
        plugin.on_transfer_rename_build(
            SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict))
        )
        assert rename_dict['season_episode'] == 'S00E83', (
            f'加更文件应回到 E83，实际 {rename_dict["season_episode"]}'
        )

        clip_dict = dict(rename_dict)
        clip_dict['original_name'] = '现在就出发 直拍.Natural.High.S03E23.Fancam.2025.1080p.WEB-DL'
        clip_dict['episode_title'] = '白敬亭直拍'
        plugin.on_transfer_rename_build(
            SimpleNamespace(event_data=SimpleNamespace(rename_dict=clip_dict))
        )
        assert clip_dict['season_episode'] == 'S00E60', (
            f'直拍文件应回到 E60，实际 {clip_dict["season_episode"]}'
        )


def test_rename_rejects_wrong_type() -> None:
    """没有指纹的旧任务，按编号回连时必须校验类型，不能把加更改到直拍集。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        store.upsert(
            Task(
                tmdb_id=231620,
                show_name='现在就出发',
                sxx_episode=60,
                sxx_name='第3季 白敬亭直拍',
                title='现在就出发 直拍.Natural.High.S03E23.Fancam.2025.1080p.WEB-DL',
                src_season=3,
                src_episode=23,
                src_site_season=3,
                src_site_episode=23,
                subtype='clip',
                subtype_word='直拍',
                status=STATUS_SUBMITTED,
            )
        )
        plugin = VarietyPilot()
        plugin._config = {'enabled': True}
        plugin._enabled = True
        plugin._store = store

        rename_dict = {
            'season': 3,
            'season_fmt': 'S03',
            'episode': 23,
            'season_episode': 'S03E23',
            'episode_title': '第5期加更',
            'name': '现在就出发',
            'original_name': '现在就出发 加更.Natural.High.S03E23.Plus.2025.2160p.WEB-DL',
        }
        plugin.on_transfer_rename_build(
            SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict))
        )
        assert rename_dict['season_episode'] == 'S03E23', (
            f'类型不符时不该改写，实际 {rename_dict["season_episode"]}'
        )


def test_rename_without_type_marker() -> None:
    """文件名里没有衍生标记时，靠「种子名包含在文件名里」回连。

    真实现场（2026-11-09）：音乐缘计划第三季先导片，站点种子名是
    `Melody Journey S03E00 2026 1080p iQIYI WEB-DL H264 AAC-ADWeb`，
    下载下来的文件却叫 `音乐缘计划.Melody.Journey.S03E00...mkv`——
    站点把中文名省了、空格换成了点。原来要求两边严格相等，
    于是回连不上，文件按原样落到 Season 3 / S03E00「第 0 集」。
    """
    seed = 'Melody Journey S03E00 2026 1080p iQIYI WEB-DL H264 AAC-ADWeb'
    filename = '音乐缘计划.Melody.Journey.S03E00.2026.1080p.iQIYI.WEB-DL.H264.AAC-ADWeb.mkv'
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        store.upsert(
            Task(
                tmdb_id=999001,
                show_name='音乐缘计划',
                sxx_episode=84,
                sxx_name='第3季 先导片：薛之谦周深刘宇宁黄子弘凡互怼',
                title=seed,
                src_season=3,
                src_episode=0,
                src_site_season=3,
                src_site_episode=0,
                subtype='prequel',
                subtype_word='先导片',
                content_key='sig|3|||prequel||',
                status=STATUS_SUBMITTED,
            )
        )

        plugin = VarietyPilot()
        plugin._config = {'enabled': True}
        plugin._enabled = True
        plugin._store = store

        rename_dict = {
            'season': 3,
            'season_fmt': 'S03',
            'episode': 0,
            'season_episode': 'S03E00',
            'episode_title': '第 0 集',
            'name': '音乐缘计划',
            'original_name': filename,
        }
        plugin.on_transfer_rename_build(
            SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict))
        )
        assert rename_dict['season_episode'] == 'S00E84', (
            f'先导片应落到 S00E84，实际 {rename_dict["season_episode"]}'
        )
        assert rename_dict['season'] == 0 and rename_dict['episode'] == 84, rename_dict


def test_rename_keeps_main_episode() -> None:
    """同剧正片不能被先导片任务改走。

    种子里省了中文名，正片文件是 `音乐缘计划.Melody.Journey.S03E01...`，
    与先导片的 S03E00 只差一个数字，宽松匹配很容易连坐。
    """
    seed = 'Melody Journey S03E00 2026 1080p iQIYI WEB-DL H264 AAC-ADWeb'
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json')
        store.upsert(
            Task(
                tmdb_id=999001,
                show_name='音乐缘计划',
                sxx_episode=84,
                sxx_name='第3季 先导片',
                title=seed,
                src_season=3,
                src_episode=0,
                src_site_season=3,
                src_site_episode=0,
                subtype='prequel',
                subtype_word='先导片',
                content_key='sig|3|||prequel||',
                status=STATUS_SUBMITTED,
            )
        )

        plugin = VarietyPilot()
        plugin._config = {'enabled': True}
        plugin._enabled = True
        plugin._store = store

        rename_dict = {
            'season': 3,
            'season_fmt': 'S03',
            'episode': 1,
            'season_episode': 'S03E01',
            'episode_title': '第 1 集',
            'name': '音乐缘计划',
            'original_name': '音乐缘计划.Melody.Journey.S03E01.2026.1080p.iQIYI.WEB-DL.H264.AAC-ADWeb.mkv',
        }
        plugin.on_transfer_rename_build(
            SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict))
        )
        assert rename_dict['season_episode'] == 'S03E01', (
            f'正片不该被改写，实际 {rename_dict["season_episode"]}'
        )


if __name__ == '__main__':
    check('加更不落直拍集', test_plus_not_into_clip)
    check('直拍不落加更集', test_clip_not_into_plus)
    check('同编号两种类型分开落位', test_same_site_number_two_types)
    check('未登记类型进入等待', test_unknown_type_waits_instead_of_stealing)
    check('整理按指纹回连任务', test_rename_matches_by_signature)
    check('整理时类型不符不改写', test_rename_rejects_wrong_type)
    check('无类型标记靠种子名回连', test_rename_without_type_marker)
    check('同剧正片不被连坐', test_rename_keeps_main_episode)
    print('全部通过：落位类型约束')
