"""整理回连与通知文案回归。

现场（2026-11-16）：
1. `Melody Journey S03E01Gathering` 下载成功但没被 MoviePilot 整理。
   任务里存的是中文剧名「音乐缘计划」，而 MP 给的 rename_dict['name']
   是 TMDB 的英文原名「Melody Journey」，按「中文名必须出现在剧名里」过滤，
   整部剧的文件都认不出，于是既没改名也没进媒体库。
2. 同一集站点发了 2160p 和 1080p 两条，任务记的是其中一条；
   文件实际下到的是另一个清晰度时，标题对不上也认不出。
3. 通知里几十行「该内容已下载过」，重点被淹没。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.store import STATUS_SUBMITTED, Task, TaskStore  # noqa: E402

TASK = Task(
    tmdb_id=999001,
    show_name='音乐缘计划',
    sxx_episode=88,
    sxx_name='第3季 第2期歌手集结：刘宇宁打地鼠手速加倍玩疯了',
    title='Melody Journey S03E01Gathering 2026 2160p iQIYI WEB-DL H265 DDP2.0-ADWeb',
    src_season=3,
    src_episode=2,
    src_site_season=3,
    src_site_episode=1,
    subtype=None,
    content_key='3||||ord|1',
    status=STATUS_SUBMITTED,
)


def _plugin() -> VarietyPilot:
    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    store.upsert(TASK)
    plugin = VarietyPilot()
    plugin._enabled = True
    plugin._store = store
    return plugin


def _rename(name: str, original: str, season: int = 3, episode: int = 1) -> dict:
    return {'season': season, 'episode': episode, 'name': name, 'original_name': original}


def test_english_show_name() -> None:
    """MP 给的剧名是英文原名时，也要能回连。"""
    plugin = _plugin()
    for name in ('音乐缘计划', 'Melody Journey'):
        task = plugin._match_task(
            _rename(name, '音乐缘计划.Melody.Journey.S03E01Gathering.2026.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv')
        )
        assert task is not None, f'剧名 {name} 没回连到任务'
        assert task.sxx_episode == 88, task.sxx_episode


def test_other_resolution() -> None:
    """任务记 2160p、实际下到 1080p，也是同一集。"""
    plugin = _plugin()
    task = plugin._match_task(
        _rename('Melody Journey', '音乐缘计划.Melody.Journey.S03E01Gathering.2026.1080p.iQIYI.WEB-DL.H264.AAC-ADWeb.mkv')
    )
    assert task is not None, '不同清晰度没回连'
    assert task.sxx_episode == 88, task.sxx_episode


def test_feature_not_matched() -> None:
    """正片不能被当成衍生内容改到 Season 00。"""
    plugin = _plugin()
    task = plugin._match_task(
        _rename('音乐缘计划', '音乐缘计划.S03E01.第2期.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv')
    )
    assert task is None, '正片被误判为衍生内容'


def test_rename_applied() -> None:
    """回连成功后要把季集号真正改写掉。"""
    plugin = _plugin()
    rename_dict = _rename(
        'Melody Journey', '音乐缘计划.Melody.Journey.S03E01Gathering.2026.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv'
    )
    plugin.on_transfer_rename_build(SimpleNamespace(event_data=SimpleNamespace(rename_dict=rename_dict)))
    assert rename_dict.get('season_episode') == 'S00E88', rename_dict
    assert rename_dict.get('season') == 0, rename_dict


def test_notice_short() -> None:
    """通知只列下载了什么，跳过的给数量。"""
    records = [
        {'show': '音乐缘计划', 'status': '已提交', 'target': 'S00E88',
         'sxx_name': '第3季 第2期歌手集结：刘宇宁打地鼠手速加倍玩疯了',
         'title': 'Melody Journey S03E01Gathering ...'},
        {'show': '现在就出发', 'status': '跳过', 'title': 'Natural High S04E11 ...',
         'reason': '该内容已下载过（S00E150《第4季 第3期上纯享·笨蛋杀手》）'},
        {'show': '现在就出发', 'status': '跳过', 'title': 'Natural High S04E10 Plus ...',
         'reason': '该内容已下载过（S00E175《第4季 第5期还有加更》）'},
        {'show': '现在就出发', 'status': '等待', 'title': 'Natural High S04E06 ...'},
    ]
    text = VarietyPilot._notice_text(records, dry=False)
    lines = text.splitlines()
    assert 'S00E88' in text, text
    assert '第3季 第2期歌手集结' in text, text
    assert '该内容已下载过' not in text, '跳过原因不该进通知'
    assert len(lines) <= 6, f'通知太长：{len(lines)} 行'
    assert '跳过 2' in text and '等待 TMDB 补齐 1' in text, text


def main() -> int:
    checks = [
        ('英文剧名回连', test_english_show_name),
        ('不同清晰度回连', test_other_resolution),
        ('正片不误改', test_feature_not_matched),
        ('整理改写生效', test_rename_applied),
        ('通知精简', test_notice_short),
    ]
    failures = []
    for name, fn in checks:
        try:
            fn()
        except AssertionError as err:
            failures.append(f'{name}：{err}')
    if failures:
        print(f'失败 {len(failures)} 项：')
        for line in failures:
            print('  -', line)
        return 1
    print('全部通过：整理回连 4 例 + 通知精简 1 例')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
