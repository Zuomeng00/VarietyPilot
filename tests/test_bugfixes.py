"""源码审查发现的 6 个 bug 的回归测试（2.1.15）。

每条都先按修复前的行为写过复现，确认能复现才改。用例按「现象」命名，
不按修复手法命名，以后重构也不容易写空。

1. 任务超限淘汰方向反了：删掉的是还没入库的活跃任务，留下的全是已完成的老记录，
   整理阶段找不到任务，文件落到正片季。
2. 一次性内容（Special / Eggs / Prologue）没副标题时，站点集号被当成真实期号：
   同一份资源带副标题能落位、不带副标题却去抢某一集。
3. 剧名自带冒号：`现在就出发：第四季 第04期纯享：小画家` 的第一个冒号被判成
   「不是片段名分隔符」后整个放弃，不同纯享片段算出同一个指纹，第二份被当重复吞掉。
4. 整理完成事件里季号已对齐成 S00，却还用源季/源集反查任务，任务永远停在「已提交」。
5. api_scan 的检查与置位不在同一把锁里：并发点两次扫描会起两条线程，重复搜站占集号。
6. 四位数季号：`S2026`（season=2026、site_season=2026）与裸写 `2026`
   （season=2026、site_season=None）算出两个内容指纹，TMDB 补录后重复下载。
"""

from __future__ import annotations

import sys
import tempfile
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.pipeline import Options, TrackedShow, scan_show, signature_of  # noqa: E402
from varietypilot.store import (  # noqa: E402
    STATUS_DONE,
    STATUS_SUBMITTED,
    Task,
    TaskStore,
)
from varietypilot.titles import parse_release  # noqa: E402


# ------------------------------------------------------------------ 1. 淘汰方向


def test_prune_keeps_active_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / 'tasks.json', max_records=200)
        for i in range(200):
            store._tasks[f'1:{i + 1:04d}'] = Task(
                tmdb_id=1, show_name='剧', sxx_episode=i + 1,
                sxx_name='n', title='t', status=STATUS_DONE,
            )
        store._tasks['1:0999'] = Task(
            tmdb_id=1, show_name='剧', sxx_episode=999,
            sxx_name='active', title='a', status=STATUS_SUBMITTED,
        )
        store._flush_locked()
        assert store.get(1, 999) is not None, '未完成的活跃任务被误淘汰'
        assert len(store.all()) == 200, f'应保留 200 条，实际 {len(store.all())}'


# ------------------------------------------------------- 2. 一次性内容无副标题


def test_oneoff_without_description_clears_episode() -> None:
    parsed = parse_release('Mao Xue Woof S01E02 Special 2026 1080p', '')
    assert parsed.episode is None, f'期号应清空，实际 {parsed.episode}'
    assert parsed.episode_source == '', f'期号来源应清空，实际 {parsed.episode_source!r}'
    assert parsed.subtype == 'special', parsed.subtype


def test_non_oneoff_without_description_keeps_episode() -> None:
    """加更、纯享这些跟某期正片绑定的类型，站点集号照旧可用。"""
    parsed = parse_release('Natural High S04E09 Plus 2026 2160p', '')
    assert parsed.episode == 9, parsed.episode


def test_oneoff_with_title_period_keeps_episode() -> None:
    """只有站点序号该清；标题里明写「第2期」是真期号，不能一起清掉。"""
    parsed = parse_release('Mao Xue Woof 第2期 Special 2026 1080p', '')
    assert parsed.episode == 2, parsed.episode
    assert parsed.episode_source == 'title', parsed.episode_source


def test_oneoff_without_description_reason_mentions_subtitle() -> None:
    """等待原因要说缺的是副标题，不能一律甩给 TMDB 缺数据。"""
    class _T:
        def __init__(self, title: str, desc: str = '') -> None:
            self.title = title
            self.description = desc
            self.site_name = '观众'
            self.size = 2 * 1024 ** 3
            self.seeders = 18
            self.downloadvolumefactor = 1.0

    class _C:
        def __init__(self, title: str, desc: str = '') -> None:
            self.torrent_info = _T(title, desc)

    from varietypilot.sxxindex import SpecialEpisode, SpecialIndex
    import time

    index = SpecialIndex(
        tmdb_id=231620,
        show_name='现在就出发',
        episodes=[SpecialEpisode(episode=175, name='第4季 第5期花絮特辑', air_date='2026-11-16')],
        season_years={4: 2026},
        search_names=['Natural High'],
        fetched_at=time.time(),
    )
    show = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])
    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    report = scan_show(
        show=show,
        index=index,
        store=store,
        options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20),
        search_fn=lambda name, sites: [_C('Natural High S04E11 Special 2026 2160p WEB-DL')],
        download_fn=lambda cand: (True, 'hash'),
    )
    rows = [r for r in report.records if r['status'] == '等待']
    assert rows, [r['status'] for r in report.records]
    assert '副标题' in rows[0]['reason'], rows[0]['reason']


# ------------------------------------------------------------ 3. 前置冒号


def test_leading_colon_keeps_segment() -> None:
    a = parse_release('Natural High S04E09 Plus 2026 2160p', '现在就出发：第四季 第04期纯享：小画家')
    b = parse_release('Natural High S04E09 Plus 2026 2160p', '现在就出发：第四季 第04期纯享：不眨眼唱歌')
    assert a.segment == '小画家', a.segment
    assert b.segment == '不眨眼唱歌', b.segment
    assert signature_of(a) != signature_of(b), '不同纯享片段的指纹不能相同'


def test_show_colon_only_is_not_segment() -> None:
    """冒号只当剧名分隔符时不能凭空造出片段名。"""
    parsed = parse_release('Natural High S04E09 Plus 2026 2160p', '现在就出发：第四季 第04期加更')
    assert parsed.segment is None, parsed.segment


# ------------------------------------------------- 4. S00 对齐后按目标集号回连


def test_transfer_complete_with_aligned_season() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        plugin = VarietyPilot.__new__(VarietyPilot)
        plugin._store = TaskStore(Path(tmp) / 'tasks.json')
        plugin._config = {'notify': False, 'refresh_library': False}
        plugin._enabled = True
        plugin._media_cache = {}
        plugin._lock = threading.RLock()
        plugin.post_message = lambda **kw: None
        plugin._refresh_library = lambda name: None
        plugin._store.upsert(Task(
            tmdb_id=999001, show_name='毛雪汪', sxx_episode=88,
            sxx_name='第2期Special', title='毛雪汪 S03E01',
            src_season=3, src_episode=1,
            status=STATUS_SUBMITTED,
        ))
        data = types.SimpleNamespace(
            download_hash='',
            mediainfo=types.SimpleNamespace(tmdb_id=999001),
            meta=types.SimpleNamespace(season=0, episode=88),
        )
        plugin.on_transfer_complete(types.SimpleNamespace(event_data=data))
        assert plugin._store.get(999001, 88).status == STATUS_DONE, '任务未标记入库'


# ------------------------------------------------------------ 5. 并发扫描


def test_concurrent_scan_single_thread() -> None:
    plugin = VarietyPilot()
    with tempfile.TemporaryDirectory() as tmp:
        plugin._store = TaskStore(Path(tmp) / 'tasks.json')
        plugin._index_store = object()
        started = []
        real_thread = threading.Thread

        class _FakeThread:
            def __init__(self, *args, **kwargs) -> None:
                self._kwargs = kwargs

            def start(self) -> None:
                started.append(self._kwargs)

        threading.Thread = _FakeThread  # type: ignore[assignment]
        try:
            first = plugin.api_scan()
            second = plugin.api_scan()
        finally:
            threading.Thread = real_thread  # type: ignore[assignment]
        assert first['success'] is True, first
        assert second['success'] is False, second
        assert len(started) == 1, f'应只启动 1 条线程，实际 {len(started)}'


def test_scan_flag_released_on_early_return() -> None:
    """未配剧集时提前返回，标志必须释放，否则扫描再也触发不了。"""
    plugin = VarietyPilot()
    with tempfile.TemporaryDirectory() as tmp:
        plugin._store = TaskStore(Path(tmp) / 'tasks.json')
        plugin._index_store = object()
        plugin._shows = []
        plugin._config = {'sync_subscribe': False}
        plugin.run_scan(claimed=True)
        assert plugin._scanning is False, '提前返回后扫描标志没有释放'


# ------------------------------------------------------ 6. 四位数季号指纹


def test_four_digit_season_signature() -> None:
    a = parse_release('Natural High S2026 Plus 2026 加更 第8期', '')
    b = parse_release('Natural High 2026 Plus 2026 加更 第8期', '')
    assert signature_of(a) == signature_of(b), (
        f'S2026 与裸写 2026 的指纹应一致：{signature_of(a)!r} != {signature_of(b)!r}'
    )


def test_normal_season_still_in_signature() -> None:
    """真正的季号仍然要进指纹，别把修复做过头。"""
    a = parse_release('Natural High S04E09 Plus 2026 2160p', '现在就出发 第四季 第09期加更 小画家')
    b = parse_release('Natural High S05E09 Plus 2026 2160p', '现在就出发 第五季 第09期加更 小画家')
    assert signature_of(a) != signature_of(b), '不同季算出同一指纹'


def main() -> int:
    checks = [
        ('超限淘汰保留活跃任务', test_prune_keeps_active_tasks),
        ('一次性内容无副标题清空期号', test_oneoff_without_description_clears_episode),
        ('加更无副标题保留期号', test_non_oneoff_without_description_keeps_episode),
        ('标题明写第X期不清空', test_oneoff_with_title_period_keeps_episode),
        ('等待原因点名缺副标题', test_oneoff_without_description_reason_mentions_subtitle),
        ('前置冒号不吞片段名', test_leading_colon_keeps_segment),
        ('剧名冒号不造片段名', test_show_colon_only_is_not_segment),
        ('S00 对齐后按目标集号回连', test_transfer_complete_with_aligned_season),
        ('并发扫描只启动一条线程', test_concurrent_scan_single_thread),
        ('提前返回释放扫描标志', test_scan_flag_released_on_early_return),
        ('S2026 与裸写 2026 指纹一致', test_four_digit_season_signature),
        ('真实季号仍进指纹', test_normal_season_still_in_signature),
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
    print(f'全部通过：审查修复 {len(checks)} 项')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
