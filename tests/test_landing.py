"""落位与判重回归：站点 Special 与 TMDB 花絮特辑、站点序号复用造成的误判。

真实现场（2026-11-10 扫描日志）：
1. `Natural High S04E04 Special`（花絮）被判「TMDB 特别季暂无对应集」挂进等待队列，
   而同一条资源在明细里明明匹配到了「第4季 第4期花絮特辑」——
   站点写 Special、TMDB 写「花絮特辑」，识别成 special 与 uncut，类型不符被裁空。
2. `S04E09 Plus`（第5期加更）被判「已下载过 S00E143《第2期下纯享·瞎掰故事王》」，
   两边唯一的共同点是站点编号都是 E09——序号兜底没校验类型。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.store import STATUS_SUBMITTED, Task, TaskStore  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.titles import parse_title  # noqa: E402

OPTIONS = Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0, prefer_resolution='2160p')


class FakeTorrent:
    def __init__(self, title: str, description: str = '', size: int = 1 * 1024 ** 3,
                 seeders: int = 30, factor: float = 1.0, site: str = '观众') -> None:
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
        SpecialEpisode(episode=138, name='第4季 第1期加更'),
        SpecialEpisode(episode=140, name='第4季 第1期花絮特辑'),
        SpecialEpisode(episode=143, name='第4季 第2期下纯享·瞎掰故事王'),
        SpecialEpisode(episode=148, name='第4季 第3期超前彩蛋'),
        SpecialEpisode(episode=157, name='第4季 第3期花絮特辑'),
        SpecialEpisode(episode=168, name='第4季 第4期花絮特辑'),
        SpecialEpisode(episode=174, name='第4季 第5期加更'),
    ],
    season_years={4: 2026},
    search_names=['Natural High'],
    fetched_at=time.time(),
)

SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])


def _scan(contexts: list, store: TaskStore):
    return scan_show(
        show=SHOW,
        index=INDEX,
        store=store,
        options=OPTIONS,
        search_fn=lambda name, sites: list(contexts),
        download_fn=lambda candidate: (True, 'hash'),
    )


def _new_store() -> TaskStore:
    return TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')


def _record(report, status: str, keyword: str):
    for item in report.records:
        if item['status'] == status and keyword in item['title']:
            return item
    return None


def test_site_special_lands_on_uncut() -> None:
    """站点写 Special、TMDB 叫「花絮特辑」，是同一集，不能挂进等待队列。"""
    store = _new_store()
    report = _scan(
        [FakeContext('Natural High S04E04 Special 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb',
                     'S04第4期 花絮')],
        store,
    )
    rec = _record(report, '已提交', 'S04E04 Special')
    assert rec is not None, [r['status'] + ' ' + r['reason'] for r in report.records]
    assert rec['target'] == 'S00E168', rec
    assert rec['sxx_name'] == '第4季 第4期花絮特辑', rec
    assert not any('暂无对应集' in line for line in report.details), report.details


def test_eggs_recognised_as_bonus() -> None:
    """站点用英文 Eggs 标彩蛋，之前认不出类型，只能靠期号乱落。"""
    parsed = parse_title('Natural High S04E03 Eggs 2026 1080p TX WEB-DL H265 AAC-ADWeb')
    assert parsed.subtype == 'bonus', parsed.subtype
    assert parsed.subtype_word == 'eggs', parsed.subtype_word

    store = _new_store()
    report = _scan(
        [FakeContext('Natural High S04E03 Eggs 2026 1080p TX WEB-DL H265 AAC-ADWeb', 'S04第3期 彩蛋')],
        store,
    )
    rec = _record(report, '已提交', 'Eggs')
    assert rec is not None, [r['reason'] for r in report.records]
    assert rec['target'] == 'S00E148', rec


def test_prologue_recognised_as_prequel() -> None:
    parsed = parse_title('Natural High S04E01 Prologue 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb')
    assert parsed.subtype == 'prequel', parsed.subtype


def test_keyed_record_not_matched_by_site_ordinal() -> None:
    """有指纹的旧记录，指纹不符就是不同内容，不能再拿站点序号去认。"""
    store = _new_store()
    store.upsert(
        Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=143,
            sxx_name='第4季 第2期下纯享·瞎掰故事王',
            title='旧纯享',
            src_site_season=4,
            src_site_episode=9,
            subtype='pure',
            content_key='sig|4|2|瞎掰故事王|pure|下|',
            status=STATUS_SUBMITTED,
        )
    )
    report = _scan(
        [FakeContext('Natural High S04E09 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb', 'S04第5期 加更')],
        store,
    )
    rec = _record(report, '已提交', 'S04E09 Plus')
    assert rec is not None, [r['status'] + ' ' + r['reason'] for r in report.records]
    assert rec['target'] == 'S00E174', rec
    assert not any('不重复下载' in (r['reason'] or '') for r in report.records), report.records


def test_unkeyed_same_type_still_blocked() -> None:
    """没有指纹的老记录（2.1.2 及更早），同类型同序号仍当已下载，避免重复下。"""
    store = _new_store()
    store.upsert(
        Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=174,
            sxx_name='第4季 第5期加更',
            title='老记录',
            src_site_season=4,
            src_site_episode=9,
            subtype='plus_bonus',
            content_key='',
            status=STATUS_SUBMITTED,
        )
    )
    report = _scan(
        [FakeContext('Natural High S04E09 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb', 'S04第5期 加更')],
        store,
    )
    rec = _record(report, '跳过', 'S04E09 Plus')
    assert rec is not None, [r['status'] for r in report.records]
    assert '不重复下载' in rec['reason'], rec


def test_unkeyed_other_type_not_blocked() -> None:
    """老记录没指纹，但类型不同，也不能拿来挡：站点序号在类型之间是复用的。"""
    store = _new_store()
    store.upsert(
        Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=143,
            sxx_name='第4季 第2期下纯享·瞎掰故事王',
            title='老纯享',
            src_site_season=4,
            src_site_episode=9,
            subtype='pure',
            content_key='',
            status=STATUS_SUBMITTED,
        )
    )
    report = _scan(
        [FakeContext('Natural High S04E09 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb', 'S04第5期 加更')],
        store,
    )
    rec = _record(report, '已提交', 'S04E09 Plus')
    assert rec is not None, [r['status'] + ' ' + r['reason'] for r in report.records]
    assert rec['target'] == 'S00E174', rec


def test_type_mismatch_reported_not_queued() -> None:
    """TMDB 有衍生集但这一季没登记该类型时，报「类型不符」，不说成「TMDB 暂无这一集」。"""
    store = _new_store()
    report = _scan(
        [FakeContext('Natural High S04E01 Prologue 2026 1080p TX WEB-DL H265 AAC-ADWeb', 'S04第1期 先导片')],
        store,
    )
    reasons = [r['reason'] for r in report.records if 'Prologue' in r['title']]
    assert reasons, report.records
    assert any('类型' in line for line in reasons), reasons
    assert not any('暂无对应集' in line for line in reasons), reasons
    assert not any('暂无对应集' in line for line in report.details), report.details


def test_miss_queue_empty_when_all_landed() -> None:
    """四条花絮全部有对应集时，等待队列必须是空的（现场就是在等待里挂了 8 条）。"""
    store = _new_store()
    contexts = [
        FakeContext('Natural High S04E01 Special 2026 1080p TX WEB-DL H265 AAC-ADWeb', 'S04第1期 花絮'),
        FakeContext('Natural High S04E03 Special 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb', 'S04第3期 花絮'),
        FakeContext('Natural High S04E04 Special 2026 1080p TX WEB-DL H265 AAC-ADWeb', 'S04第4期 花絮'),
    ]
    report = _scan(contexts, store)
    assert not any(r['status'] == '等待' for r in report.records), [
        (r['title'], r['reason']) for r in report.records
    ]
    assert store.misses() == [], [m.title for m in store.misses()]


def test_landed_resource_leaves_miss_queue() -> None:
    """之前挂在「等待补齐」里的资源，这一轮匹配上了就要从等待列表撤掉。"""
    from varietypilot.store import MissRecord

    store = _new_store()
    title = 'Natural High S04E03 Special 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb'
    store.record_miss(MissRecord(tmdb_id=231620, show_name='现在就出发', title=title,
                                 reason='TMDB 特别季暂无对应集，等待补齐'))
    assert len(store.misses()) == 1

    _scan([FakeContext(title, 'S04第3期 花絮')], store)
    assert store.misses() == [], [m.title for m in store.misses()]


def test_season_pack_not_queued() -> None:
    """整季包不进衍生链路，也不该挂在等待列表里。"""
    store = _new_store()
    report = _scan(
        [FakeContext('[大陆][2023][现在就出发][Natural.High.S01.2023.2160p.WEB-DL.H265.AAC-TJUPT]',
                     '现在就出发 第一季 加更')],
        store,
    )
    assert not any('S01.2023' in r['title'] and r['status'] == '等待' for r in report.records), report.records
    assert store.misses() == []


def check(name: str, func) -> None:
    func()
    print(f'通过：{name}')


if __name__ == '__main__':
    check('站点 Special 落到花絮特辑', test_site_special_lands_on_uncut)
    check('Eggs 识别成彩蛋', test_eggs_recognised_as_bonus)
    check('Prologue 识别成先导片', test_prologue_recognised_as_prequel)
    check('有指纹的记录不被站点序号误判', test_keyed_record_not_matched_by_site_ordinal)
    check('无指纹同类型仍判重', test_unkeyed_same_type_still_blocked)
    check('无指纹不同类型不判重', test_unkeyed_other_type_not_blocked)
    check('类型不符报类型而非等待', test_type_mismatch_reported_not_queued)
    check('全部落位时等待队列为空', test_miss_queue_empty_when_all_landed)
    check('落位后从等待列表撤掉', test_landed_resource_leaves_miss_queue)
    check('整季包不进等待列表', test_season_pack_not_queued)
    print('全部通过：落位兼容 + 判重兜底')
