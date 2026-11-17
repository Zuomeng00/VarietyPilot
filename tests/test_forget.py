"""删除任务记录的回归：删掉记录后内容才会重新下载。

现场（2026-11-17）：音乐人来了被整理成 S00E88，占掉了歌手集结那一集。
歌手集结的种子于是被「该内容已下载过（S00E88）」挡住，不再下载。
要让它重下，只能把这个任务记录删掉；而记录常驻内存，手工改 tasks.json
会被插件下一次写入覆盖，所以必须提供插件内的删除入口。

这一组用例守住四件事：
1. 按剧名 / TMDB ID / 通配符都能定位到记录；
2. 删掉之后同一集的新记录不会被重复删（配置留着也安全）；
3. 找不到时给明确回执，不静默成功；
4. 只删记录，不动文件、不动同剧其他集。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import TrackedShow  # noqa: E402
from varietypilot.store import STATUS_SUBMITTED, Task, TaskStore  # noqa: E402


def _task(episode: int, name: str, title: str) -> Task:
    return Task(
        tmdb_id=231620,
        show_name='音乐缘计划',
        sxx_episode=episode,
        sxx_name=name,
        title=title,
        src_season=3,
        src_episode=1,
        src_site_season=3,
        src_site_episode=1,
        subtype=None,
        content_key=f'sig|{episode}',
        status=STATUS_SUBMITTED,
    )


def _plugin() -> VarietyPilot:
    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    for task in (
        _task(86, '第3季 第1期音乐人来了', 'Melody Journey S03E01 Coming 2026 2160p'),
        _task(87, '第3季 第1期全纪实', 'Melody Journey S03E01Documentary 2026 2160p'),
        _task(88, '第3季 第2期歌手集结', 'Melody Journey S03E01Gathering 2026 2160p'),
    ):
        store.upsert(task)
    plugin = VarietyPilot()
    plugin._enabled = True
    plugin._store = store
    plugin._shows = [
        TrackedShow(name='音乐缘计划', tmdb_id=231620, aliases=['Melody Journey'], seasons=[3])
    ]
    return plugin


def test_forget_by_show_name() -> None:
    plugin = _plugin()
    note = plugin._apply_forget('音乐缘计划:S00E88')
    assert note.startswith('已删除'), note
    episodes = sorted(task.sxx_episode for task in plugin._store.all())
    assert episodes == [86, 87], episodes


def test_forget_by_tmdb_id() -> None:
    plugin = _plugin()
    note = plugin._apply_forget('231620:S00E88')
    assert note.startswith('已删除'), note
    assert plugin._store.get(231620, 88) is None


def test_forget_accepts_chinese_colon_and_multi() -> None:
    """中文冒号、逗号分隔、S00 前缀都要认，用户从页面抄下来就能用。"""
    plugin = _plugin()
    note = plugin._apply_forget('音乐缘计划：S00E86, 231620:S00E87')
    assert 'S00E86' in note and 'S00E87' in note, note
    assert [task.sxx_episode for task in plugin._store.all()] == [88]


def test_forget_wildcard() -> None:
    plugin = _plugin()
    note = plugin._apply_forget('*:S00E86')
    assert note.startswith('已删除'), note
    assert plugin._store.get(231620, 86) is None


def test_forget_is_not_repeated() -> None:
    """配置里留着同一段文字，不会把之后重新下载生成的记录再删一次。"""
    plugin = _plugin()
    plugin._apply_forget('音乐缘计划:S00E88')
    plugin._store.upsert(_task(88, '第3季 第2期歌手集结（重下）', 'Melody Journey S03E01Gathering 2160p'))
    assert plugin._apply_forget('音乐缘计划:S00E88') == ''
    assert plugin._store.get(231620, 88) is not None


def test_forget_missing_returns_note() -> None:
    plugin = _plugin()
    note = plugin._apply_forget('999999:S00E99')
    assert note.startswith('没找到'), note
    assert len(plugin._store.all()) == 3


def test_forget_ignores_bad_input() -> None:
    """没有集号、只有半边写法时不做任何事，避免误删整部剧。"""
    plugin = _plugin()
    assert plugin._apply_forget('音乐缘计划') == ''
    assert plugin._apply_forget('音乐缘计划:') == ''
    assert len(plugin._store.all()) == 3


def main() -> int:
    checks = [
        ('按剧名删除', test_forget_by_show_name),
        ('按 TMDB ID 删除', test_forget_by_tmdb_id),
        ('中文冒号与多条', test_forget_accepts_chinese_colon_and_multi),
        ('通配符删除', test_forget_wildcard),
        ('不重复删', test_forget_is_not_repeated),
        ('找不到时回执', test_forget_missing_returns_note),
        ('错误写法不误删', test_forget_ignores_bad_input),
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
    print(f'全部通过：删除任务记录 {len(checks)} 项')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
