"""跨综艺整理回连回归：不能只对某一部综艺有效。

现场（2026-11-16）：音乐缘计划的 `Melody Journey S03E01Gathering` 下载成功却没整理。
修完那一部之后，用国内常见综艺的命名形态各造一条，发现同类问题一片：

1. 压制组名位置错位。种子写 `... H265 DDP2.0-ADWeb`（尾缀），下载下来是
   `[ADWeb].Go.Fighting.S10E03...`（前缀），整串比对永远失败，整部剧认不回任务。
2. 年度季号 `S2026E08`（你好星期六、歌手、中国好声音）解析不出季号，
   后续按季号过滤时整条被丢掉。
3. MP 给英文原名（音乐缘计划 → Melody Journey、种地吧 → Become a Farmer），
   任务里存中文名，按「中文名必须出现在剧名里」过滤会整部失效。
4. 同一条内容换个清晰度重发，任务记 2160p、实际下到 1080p。
5. 同一站点编号下挂多条不同类型的任务，文件名却没标类型 —— 挑错就会
   把加更改到纯享那一集，所以要主动放弃，而不是猜一个。

这一组用例的作用是：以后再改回连逻辑，跑一遍就知道有没有只对个案生效。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.store import STATUS_QUEUED, STATUS_SUBMITTED, Task, TaskStore  # noqa: E402

# (中文名, 种子名, 下载后的文件名, MP 给的剧名, 季, 集, 类型)
LANDED = [
    (
        '现在就出发',
        'Natural High S04E09 Plus 2026 2160p WEB-DL',
        '现在就出发.Natural.High.S04E09.Plus.2026.2160p.WEB-DL.mkv',
        'Natural High', 4, 9, 'plus_bonus',
    ),
    (
        '奔跑吧',
        'Keep Running S13E05P 2026 2160p WEB-DL H265',
        'Keep.Running.S13E05P.2026.2160p.WEB-DL.H265-GROUP.mkv',
        'Keep Running', 13, 5, 'pure',
    ),
    (
        # 组名从尾缀挪到前缀
        '极限挑战',
        'Go Fighting S10E03 Plus 2026 2160p WEB-DL H265 DDP2.0-ADWeb',
        '[ADWeb].Go.Fighting.S10E03.Plus.2026.2160p.WEB-DL.H265.DDP2.0.mkv',
        'Go Fighting', 10, 3, 'plus_bonus',
    ),
    (
        # 年度季号
        '你好星期六',
        'Hello Saturday S2026E08 2160p WEB-DL H265',
        '你好星期六.Hello.Saturday.S2026E08.2160p.WEB-DL.H265.mkv',
        'Hello Saturday', 2026, 8, 'plus_bonus',
    ),
    (
        '大侦探',
        "Who's the Murderer S10E05 2026 2160p WEB-DL",
        'Whos.the.Murderer.S10E05.2026.2160p.WEB-DL.mkv',
        "Who's the Murderer", 10, 5, 'plus_bonus',
    ),
    (
        # 中文名带年份、英文名不带
        '歌手2026',
        'Singer S2026E05 2160p WEB-DL',
        '歌手2026.Singer.S2026E05.2160p.WEB-DL.mkv',
        'Singer', 2026, 5, 'plus_bonus',
    ),
    (
        # 文件名里多一段译名
        '种地吧',
        'Become a Farmer S03E07 2026 2160p WEB-DL',
        '种地吧.少年篇.Become.a.Farmer.S03E07.2026.2160p.WEB-DL.mkv',
        'Become a Farmer', 3, 7, 'plus_bonus',
    ),
    (
        '脱口秀和Ta的朋友们',
        'Rock and Roast S03E06 2026 2160p WEB-DL',
        '脱口秀和Ta的朋友们.Rock.and.Roast.S03E06.2026.2160p.WEB-DL.mkv',
        'Rock and Roast', 3, 6, 'plus_bonus',
    ),
    (
        # 站点把正片和衍生放一套编号里数
        '密室大逃脱',
        'Great Escape S06E04 2026 2160p WEB-DL',
        'Great.Escape.S06E04.2026.2160p.WEB-DL.mkv',
        '密室大逃脱', 6, 4, 'plus_bonus',
    ),
    (
        '乘风2026',
        'Sisters Who Make Waves S06E02 2026 2160p WEB-DL',
        '乘风2026.Sisters.Who.Make.Waves.S06E02.2026.2160p.WEB-DL.mkv',
        'Sisters Who Make Waves', 6, 2, 'plus_bonus',
    ),
]


def _store(*tasks: Task) -> TaskStore:
    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    for task in tasks:
        store.upsert(task)
    return store


def _plugin(*tasks: Task) -> VarietyPilot:
    plugin = VarietyPilot()
    plugin._enabled = True
    plugin._store = _store(*tasks)
    return plugin


def _task(cn: str, seed: str, season: int, episode: int, subtype: str,
          subtype_word: str = '', **extra) -> Task:
    return Task(
        tmdb_id=999001,
        show_name=cn,
        sxx_episode=100,
        sxx_name=f'第{season}季 第{episode}期加更',
        title=seed,
        src_season=season,
        src_episode=episode,
        src_site_season=extra.pop('src_site_season', season),
        src_site_episode=extra.pop('src_site_episode', episode),
        subtype=subtype,
        subtype_word=subtype_word,
        content_key='',
        status=STATUS_SUBMITTED,
        **extra,
    )


def _rename(name: str, original: str, season: int, episode: int) -> dict:
    return {'season': season, 'episode': episode, 'name': name, 'original_name': original}


# --------------------------------------------------------------- 正例


def test_multi_show_landing() -> None:
    """十部综艺、十种命名形态，都要能认回任务。"""
    misses = []
    for cn, seed, filename, mp_name, season, episode, subtype in LANDED:
        task = _task(cn, seed, season, episode, subtype)
        plugin = _plugin(task)
        hit = plugin._match_task(_rename(mp_name, filename, season, episode))
        if hit is None or hit.sxx_episode != 100:
            misses.append(f'{cn}（{mp_name}）')
    assert not misses, '未回连：' + '、'.join(misses)


def test_group_name_moved() -> None:
    """组名从种子尾缀挪到文件名前缀，仍然是同一条。"""
    task = _task('极限挑战', 'Go Fighting S10E03 Plus 2026 2160p WEB-DL H265 DDP2.0-ADWeb',
                 10, 3, 'plus_bonus', 'Plus')
    plugin = _plugin(task)
    for filename in (
        '[ADWeb].Go.Fighting.S10E03.Plus.2026.2160p.WEB-DL.H265.DDP2.0.mkv',
        'Go.Fighting.S10E03.Plus.2026.2160p.WEB-DL.H265.DDP2.0-ADWeb.mkv',
    ):
        assert plugin._match_task(_rename('Go Fighting', filename, 10, 3)) is not None, filename


def test_year_season_parsed() -> None:
    """年度季号必须能解析出季号，否则按季过滤时整条被丢。"""
    from varietypilot.titles import parse_title

    parsed = parse_title('你好星期六.Hello.Saturday.S2026E08.2160p.WEB-DL.mkv')
    assert parsed.season == 2026, parsed.season
    assert parsed.episode == 8, parsed.episode
    assert parsed.site_season == 2026, parsed.site_season


def test_year_season_renamed() -> None:
    """年度季号剧的路径要整段改到 S00，目录也不能留在 Season 2026。"""
    from varietypilot.renaming import rewrite_render_str

    cases = [
        ('/tv/你好星期六 (2026)/Season 2026/你好星期六 - S2026E08 - 第8期.mkv', 174,
         '/tv/你好星期六 (2026)/Season 00/你好星期六 - S00E174 - 第8期.mkv'),
        ('/tv/歌手 (2026)/Season 2026/歌手 - S2026E05.mkv', 88,
         '/tv/歌手 (2026)/Season 00/歌手 - S00E88.mkv'),
        ('/tv/现在就出发 (2025)/Season 4/现在就出发 - S04E09 Plus.mkv', 174,
         '/tv/现在就出发 (2025)/Season 00/现在就出发 - S00E174 Plus.mkv'),
    ]
    for raw, episode, expect in cases:
        got = rewrite_render_str(raw, episode)
        assert got == expect, f'{raw} → {got}'


def test_year_season_scoring() -> None:
    """年度季号的候选不能被判成「季不符」而压分。"""
    import time

    from varietypilot.sxxindex import SpecialEpisode, SpecialIndex, rank_specials
    from varietypilot.titles import parse_release

    index = SpecialIndex(
        tmdb_id=999001,
        show_name='你好星期六',
        episodes=[
            SpecialEpisode(episode=300, name='2026 第8期 加更', air_date='2026-11-14'),
            SpecialEpisode(episode=301, name='2026 第9期 加更', air_date='2026-11-21'),
        ],
        season_years={8: 2026},
        search_names=['Hello Saturday'],
        fetched_at=time.time(),
    )
    parsed = parse_release(
        'Hello Saturday S2026E08 2160p WEB-DL', '你好星期六 2026 第08期 加更 20261114'
    )
    results = rank_specials(parsed, index)
    assert results, '年度季号的候选全被筛掉了'
    assert results[0].episode == 300, results[0].describe()
    assert not any('季不符' in line for line in results[0].reasons), results[0].reasons


def test_same_site_number_selects_right_one() -> None:
    """站点同一编号下挂多条内容时，不能取第一条就改。

    音乐缘计划把每期的衍生都编成 S03E01，靠英文后缀区分：
    Coming（音乐人来了）/ Gathering（歌手集结）/ Documentary（全纪实）。
    现场就是只按编号取第一条，把 S00E86 的「音乐人来了」改成了
    S00E88 的「第 88 集」。
    """
    cases = [
        # (任务集号, 集名, 种子名, 文件名, 期望)
        (
            86, '第3季 第1期音乐人来了：岳薇诉说宇宙孤寂感',
            'Melody Journey S03E01 Coming 2026 2160p iQIYI WEB-DL H265 DDP2.0-ADWeb',
            '音乐缘计划.音乐人来了.Melody.Journey.S03E01.Coming.2026.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv',
            86,
        ),
        (
            87, '第3季 第1期全纪实：薛之谦被花式挑衅',
            'Melody Journey S03E01Documentary 2026 2160p iQIYI WEB-DL H265 DDP2.0-ADWeb',
            '音乐缘计划.Melody.Journey.S03E01Documentary.2026.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv',
            87,
        ),
        (
            88, '第3季 第2期歌手集结：刘宇宁打地鼠手速加倍玩疯了',
            'Melody Journey S03E01Gathering 2026 2160p iQIYI WEB-DL H265 DDP2.0-ADWeb',
            '音乐缘计划.Melody.Journey.S03E01Gathering.2026.2160p.iQIYI.WEB-DL.H265.DDP2.0-ADWeb.mkv',
            88,
        ),
    ]
    tasks = [
        _task('音乐缘计划', seed, 3, 1, None)
        for _, _, seed, _, _ in cases
    ]
    for task, (episode, name, _, _, _) in zip(tasks, cases):
        task.sxx_episode = episode
        task.sxx_name = name
    tasks[0].subtype, tasks[0].subtype_word = 'extra', '音乐人来了'
    tasks[1].subtype, tasks[1].subtype_word = 'uncut', '全纪实'
    tasks[2].subtype, tasks[2].subtype_word = None, ''

    plugin = _plugin(*tasks)
    wrong = []
    for episode, _, _, filename, expect in cases:
        hit = plugin._match_task(_rename('音乐缘计划', filename, 3, 1))
        got = hit.sxx_episode if hit else None
        if got != expect:
            wrong.append(f'期望 S00E{expect} 实际 {got}')
    assert not wrong, '；'.join(wrong)


def test_letter_suffix_not_swapped() -> None:
    """站点的字母后缀（S13E05P 纯享 / S13E05 正片衍生）也要能分开。"""
    plain = _task('奔跑吧', 'Keep Running S13E05 2026 2160p WEB-DL', 13, 5, 'plus_bonus')
    plain.sxx_episode = 200
    pure = _task('奔跑吧', 'Keep Running S13E05P 2026 2160p WEB-DL', 13, 5, 'pure')
    pure.sxx_episode = 201
    plugin = _plugin(plain, pure)

    hit = plugin._match_task(
        _rename('Keep Running', 'Keep.Running.S13E05P.2026.2160p.WEB-DL.mkv', 13, 5)
    )
    assert hit is not None and hit.sxx_episode == 201, f'纯享落错：{hit.sxx_episode if hit else None}'

    hit = plugin._match_task(
        _rename('Keep Running', 'Keep.Running.S13E05.2026.2160p.WEB-DL.mkv', 13, 5)
    )
    assert hit is not None and hit.sxx_episode == 200, f'加更落错：{hit.sxx_episode if hit else None}'


def test_type_from_description_only() -> None:
    """类型只写在副标题里、种子名和文件名都不带类型词，也要能认回。"""
    task = _task('音乐缘计划', 'Melody Journey S03E01 2026 1080p iQIYI WEB-DL', 3, 1, 'uncut')
    plugin = _plugin(task)
    hit = plugin._match_task(
        _rename('Melody Journey', '音乐缘计划.Melody.Journey.S03E01.2026.1080p.iQIYI.WEB-DL.mkv', 3, 1)
    )
    assert hit is not None, '类型只在副标题时没回连'


def test_legacy_task_without_site_numbers() -> None:
    """更早版本的记录没有站点编号，退回真实期号也要能认。"""
    task = _task('大侦探', "Who's the Murderer S10E05 2026 2160p WEB-DL", 10, 5,
                 'plus_bonus', src_site_season=None, src_site_episode=None)
    plugin = _plugin(task)
    hit = plugin._match_task(_rename('大侦探', 'Whos.the.Murderer.S10E05.2026.2160p.WEB-DL.mkv', 10, 5))
    assert hit is not None, '老记录没回连'


def test_hash_match_wins() -> None:
    """事件里能拿到下载 hash 时按 hash 定位，标题再乱也不会认错。"""
    task = _task('极限挑战', 'Go Fighting S10E03 Plus 2026 2160p WEB-DL', 10, 3,
                 'plus_bonus', 'Plus', download_hash='abc123')
    plugin = _plugin(task)
    data = SimpleNamespace(rename_dict=_rename('随便叫什么', 'video.mkv', 10, 3), download_hash='abc123')
    hit = plugin._task_by_event_hash(data, data.rename_dict)
    assert hit is not None and hit.sxx_episode == 100, hit

    # hash 对不上时不能乱认
    data = SimpleNamespace(rename_dict=_rename('随便叫什么', 'video.mkv', 10, 3), download_hash='zzz')
    assert plugin._task_by_event_hash(data, data.rename_dict) is None


# --------------------------------------------------------------- 反例


def test_feature_not_touched() -> None:
    """正片绝不能被改到 Season 00。"""
    task = _task('极限挑战', 'Go Fighting S10E03 Plus 2026 2160p WEB-DL H265 DDP2.0-ADWeb',
                 10, 3, 'plus_bonus', 'Plus')
    plugin = _plugin(task)
    for filename in (
        # 同号正片：没有 Plus 标记
        '[ADWeb].Go.Fighting.S10E03.2026.2160p.WEB-DL.H265.DDP2.0.mkv',
        '极限挑战.第10季.S10E03.第3期.2160p.WEB-DL.mkv',
        # 邻集正片
        '[ADWeb].Go.Fighting.S10E04.2026.2160p.WEB-DL.H265.DDP2.0.mkv',
    ):
        assert plugin._match_task(_rename('极限挑战', filename, 10, 3)) is None, filename


def test_ambiguous_site_number_gives_up() -> None:
    """同号挂多条不同类型任务、文件又没标类型时，放弃而不是猜。"""
    plus = _task('极限挑战', 'Go Fighting S10E03 Plus 2026 2160p WEB-DL', 10, 3,
                 'plus_bonus', 'Plus')
    pure = _task('极限挑战', 'Go Fighting S10E03 Pure 2026 2160p WEB-DL', 10, 3, 'pure', 'Pure')
    pure.sxx_episode = 101
    plugin = _plugin(plus, pure)
    hit = plugin._match_task(
        _rename('极限挑战', '[ADWeb].Go.Fighting.S10E03.2026.2160p.WEB-DL.mkv', 10, 3)
    )
    assert hit is None, f'同号两条任务却挑了一条：{hit.sxx_episode if hit else None}'


def main() -> int:
    checks = [
        ('十部综艺回连', test_multi_show_landing),
        ('组名位置错位', test_group_name_moved),
        ('同号多内容分流', test_same_site_number_selects_right_one),
        ('字母后缀不串', test_letter_suffix_not_swapped),
        ('年度季号解析', test_year_season_parsed),
        ('年度季号路径改写', test_year_season_renamed),
        ('年度季号落位打分', test_year_season_scoring),
        ('类型只在副标题', test_type_from_description_only),
        ('老记录无站点编号', test_legacy_task_without_site_numbers),
        ('hash 精确定位', test_hash_match_wins),
        ('正片不改写', test_feature_not_touched),
        ('同号歧义放弃', test_ambiguous_site_number_gives_up),
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
    print(f'全部通过：{len(LANDED)} 部综艺回连 + 10 项边界与反例')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
