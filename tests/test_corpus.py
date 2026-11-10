"""跨节目语料回归：换一部综艺，命名换成另一套说法，还能不能认对。

这份语料不是「现在就出发」一家的写法，而是把国内主流综艺在 PT 站点上的
衍生内容命名拉了一遍：加更、纯享、彩蛋、花絮、直拍、先导、特辑、直播、
衍生番，每类都取五六种不同叫法，覆盖 20 部节目。

两类用例：
1. 认得出类型的——必须认对，认错就会落错位；
2. 认不出类型的——必须**不落位**，进等待队列，而不是按分数瞎猜。
   词表永远追不上站点造词，所以「认不出就等」是这条链路的最后一道闸。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot.pipeline import Candidate, Options, TrackedShow, assign_episodes, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex, rank_specials  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402
from varietypilot.titles import parse_release  # noqa: E402

# (资源名, 站点副标题, 期望类型)
CORPUS = [
    # 现在就出发：站点英文词 + 中文副标题
    ('Natural High S04E09 Plus 2026 2160p WEB-DL', '现在就出发 第四季 加更 第05期加更：白敬亭林一雷霆画功', 'plus_bonus'),
    ('Natural High S04E10 Eggs 2026 1080p WEB-DL', '现在就出发 第四季 彩蛋 第05期彩蛋', 'bonus'),
    ('Natural High S04E11 Fancam 2026 1080p WEB-DL', '现在就出发 第四季 直拍 第05期黄景瑜直拍', 'clip'),
    ('Natural High S04E12 Special 2026 1080p WEB-DL', '现在就出发 第四季 花絮 第05期花絮特辑', 'uncut'),
    # 奔跑吧
    ('Keep Running S13E05 2026 1080p WEB-DL', '奔跑吧 第十三季 第05期 会员Plus版', 'plus_bonus'),
    ('Keep Running S13E05P 2026 1080p WEB-DL', '奔跑吧 第十三季 第05期 纯享版', 'pure'),
    ('Keep Running S13E05B 2026 1080p WEB-DL', '奔跑吧 第十三季 第05期 未播花絮', 'uncut'),
    ('Keep Running S13E05SP 2026 1080p WEB-DL', '奔跑吧 第十三季 第05期 特别企划', 'special'),
    # 极限挑战
    ('Go Fighting S10E03 2026 1080p WEB-DL', '极限挑战 第十季 第03期 加更版', 'plus_bonus'),
    ('Go Fighting S10E03C 2026 1080p WEB-DL', '极限挑战 第十季 第03期 彩蛋', 'bonus'),
    # 王牌对王牌
    ('Ace vs Ace S09E02 2026 1080p WEB-DL', '王牌对王牌 第九季 第02期 会员悠享版', 'plus_bonus'),
    ('Ace vs Ace S09E02X 2026 1080p WEB-DL', '王牌对王牌 第九季 第02期 独家直拍', 'clip'),
    # 你好星期六（用年份当季号）
    ('Hello Saturday S2026E08 1080p WEB-DL', '你好星期六 2026 第08期 加更', 'plus_bonus'),
    ('Hello Saturday S2026E08M 1080p WEB-DL', '你好星期六 2026 第08期 名场面特辑', 'special'),
    # 密室大逃脱
    ('Great Escape S06E04 2026 1080p WEB-DL', '密室大逃脱 第六季 第04期 会员版', 'plus_bonus'),
    ('Great Escape S06E04R 2026 1080p WEB-DL', '密室大逃脱 第六季 第04期 复盘特辑', 'special'),
    # 种地吧
    ('Become a Farmer S03E07 2026 1080p WEB-DL', '种地吧 第三季 第07期 加更', 'plus_bonus'),
    ('Become a Farmer S03E07L 2026 1080p WEB-DL', '种地吧 第三季 第07期 直播回看', 'live'),
    # 声生不息
    ('Infinity and Beyond S04E03 2026 1080p WEB-DL', '声生不息 第四季 第03期 纯享版', 'pure'),
    ('Infinity and Beyond S04E03S 2026 1080p WEB-DL', '声生不息 第四季 第03期 舞台直拍', 'clip'),
    # 乘风 / 披荆斩棘
    ('Sisters Who Make Waves S06E02 2026 1080p WEB-DL', '乘风2026 第02期 会员加更版', 'plus_bonus'),
    ('Sisters Who Make Waves S06E02F 2026 1080p WEB-DL', '乘风2026 第02期 花絮', 'uncut'),
    ('Call Me By Fire S05E01 2026 1080p WEB-DL', '披荆斩棘 第五季 第01期 超前企划', 'special'),
    ('Call Me By Fire S05E01P 2026 1080p WEB-DL', '披荆斩棘 第五季 第01期 抢鲜版', 'prequel'),
    # 歌手（年份季号，且副标题不含「第X季」）
    ('Singer S2026E05 1080p WEB-DL', '歌手2026 第05期 加更版', 'plus_bonus'),
    ('Singer S2026E05U 1080p WEB-DL', '歌手2026 第05期 未删减版', 'plus_bonus'),
    # 天赐的声音 / 脱口秀 / 喜人奇妙夜
    ('The Treasured Voice S06E03 1080p WEB-DL', '天赐的声音 第六季 第03期 纯享', 'pure'),
    ('Rock & Roast S03E06 1080p WEB-DL', '脱口秀和Ta的朋友们 第三季 第06期 加更', 'plus_bonus'),
    ('Rock & Roast S03E06E 1080p WEB-DL', '脱口秀和Ta的朋友们 第三季 第06期 彩蛋', 'bonus'),
    ('Comedy Night S02E04 1080p WEB-DL', '喜人奇妙夜 第二季 第04期 加更版', 'plus_bonus'),
    ('Comedy Night S02E04X 1080p WEB-DL', '喜人奇妙夜 第二季 第04期 未播片段', 'uncut'),
    # 大侦探（含「案情回顾」这类非标准叫法）
    ("Who's the Murderer S10E05 1080p WEB-DL", '大侦探 第十季 第05期 会员Plus', 'plus_bonus'),
    ("Who's the Murderer S10E05D 1080p WEB-DL", '大侦探 第十季 第05期 案情回顾', 'special'),
    # 桃花坞 / 你好生活 / 好声音 / 喜剧大赛 / 快乐再出发
    ('Peach Blossom S04E02 1080p WEB-DL', '五十公里桃花坞 第四季 第02期 加更', 'plus_bonus'),
    ('Hello Life S04E01 1080p WEB-DL', '你好生活 第四季 第01期 加长版', 'plus_bonus'),
    ('The Voice of China S2026E03 1080p WEB-DL', '中国好声音2026 第03期 盲选花絮', 'uncut'),
    ('Annual Comedy S03E02 1080p WEB-DL', '一年一度喜剧大赛 第三季 第02期 纯享版', 'pure'),
    ('Happy Reunion S02E05 1080p WEB-DL', '快乐再出发 第二季 第05期 加更', 'plus_bonus'),
]

# 认不出的写法：站点自造词，词表不可能穷举
UNKNOWN = [
    ('Natural High S04E09 Diary 2026 2160p WEB-DL', '现在就出发 第四季 第05期 坞民日记 20261108'),
    ('Natural High S04E09 Mix 2026 2160p WEB-DL', '现在就出发 第四季 第05期 花式剪辑'),
    ('Keep Running S13E05Q 2026 1080p WEB-DL', '奔跑吧 第十三季 第05期 状态记录'),
]


def _failures() -> list[str]:
    bad: list[str] = []
    for title, desc, expect in CORPUS:
        parsed = parse_release(title, desc)
        if parsed.subtype != expect:
            bad.append(f'{desc}: 期望 {expect}，实际 {parsed.subtype}（{parsed.subtype_word}）')
    return bad


def check_corpus() -> None:
    bad = _failures()
    assert not bad, '类型识别回归：\n' + '\n'.join(bad)


def check_episode_from_desc() -> None:
    """副标题的「第X期」必须压过站点序号，否则整批资源都会错位一期。"""
    bad: list[str] = []
    for title, desc, _ in CORPUS:
        parsed = parse_release(title, desc)
        if parsed.episode is None:
            bad.append(f'{desc}: 没读出期号')
        elif parsed.episode_is_site_ordinal:
            bad.append(f'{desc}: 期号仍来自站点序号')
    assert not bad, '期号来源回归：\n' + '\n'.join(bad)


def check_unknown_has_no_subtype() -> None:
    """确认这几条确实是「认不出」，用来守住后面的兜底用例。"""
    for title, desc in UNKNOWN:
        parsed = parse_release(title, desc)
        assert parsed.subtype is None, f'{desc} 已被识别为 {parsed.subtype}，换个未收录的写法'


# ---------------------------------------------------------------- 落位兜底

OPTIONS = Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0, prefer_resolution='2160p')


class FakeTorrent:
    def __init__(self, title: str, description: str = '', size: int = 1024 ** 3,
                 seeders: int = 30, factor: float = 1.0) -> None:
        self.title = title
        self.description = description
        self.site_name = '观众'
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = factor


class FakeContext:
    def __init__(self, title: str, description: str = '') -> None:
        self.torrent_info = FakeTorrent(title, description)


def _index() -> SpecialIndex:
    """第 5 期同时有加更、花絮、纯享三条；第 6 期只有一条加更。"""
    return SpecialIndex(
        tmdb_id=231620,
        show_name='现在就出发',
        episodes=[
            SpecialEpisode(episode=174, name='第4季 第5期加更', air_date='2026-11-08'),
            SpecialEpisode(episode=175, name='第4季 第5期花絮特辑', air_date='2026-11-08'),
            SpecialEpisode(episode=176, name='第4季 第5期纯享·小画家', air_date='2026-11-08'),
            SpecialEpisode(episode=180, name='第4季 第6期加更', air_date='2026-11-15'),
        ],
        season_years={4: 2026},
        search_names=['Natural High'],
        fetched_at=time.time(),
    )


SHOW = TrackedShow(name='现在就出发', tmdb_id=231620, aliases=['Natural High'], seasons=[4])


def _scan(contexts: list) -> 'object':
    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    return scan_show(
        show=SHOW,
        index=_index(),
        store=store,
        options=OPTIONS,
        search_fn=lambda name, sites: list(contexts),
        download_fn=lambda candidate: (True, 'hash'),
    )


def _candidate(title: str, desc: str) -> 'object':
    parsed = parse_release(title, desc)
    options = rank_specials(parsed, _index())
    assert options, f'{desc}: 没有候选集'
    return Candidate(
        title=title, site='观众', size=1024 ** 3, seeders=30, context=None,
        parsed=parsed, match=options[0], description=desc, options=options, tmdb_id=231620,
    )


def check_untyped_not_landed() -> None:
    """认不出类型的资源不能按分数抢已有类型的集。

    第 5 期在 TMDB 里有加更、花絮、纯享三条，「坞民日记」是哪一条不知道，
    硬落就是把内容塞错地方，还得人工进媒体库收拾。这种情况必须进等待。
    """
    candidate = _candidate('Natural High S04E09 Diary 2026 2160p WEB-DL', '现在就出发 第四季 第05期 坞民日记 20261108')
    chosen, skipped = assign_episodes([candidate])
    assert not chosen, [f'S00E{item.match.episode:02d}' for item in chosen]
    assert skipped and '类型' in skipped[0][1], skipped


def check_untyped_landed_when_unambiguous() -> None:
    """这一期只有一条集时，期号对上就等于位置唯一，可以落。"""
    candidate = _candidate('Natural High S04E10 Diary 2026 2160p WEB-DL', '现在就出发 第四季 第06期 坞民日记 20261115')
    chosen, skipped = assign_episodes([candidate])
    assert chosen, skipped
    assert chosen[0].match.episode == 180, chosen[0].match.describe()


def check_unkeyed_without_subtype_not_blocking() -> None:
    """更早版本的记录没存类型，不能凭站点序号把不同类的资源判成已下载。

    现场：`S04E09 第5期加更` 被判「已下载过 S00E143《第2期下纯享·瞎掰故事王》」，
    两条唯一的共同点就是站点序号都是 E09——序号在类型之间是复用的。
    """
    from varietypilot.store import STATUS_SUBMITTED, Task

    store = TaskStore(Path(tempfile.mkdtemp()) / 'tasks.json')
    store.upsert(
        Task(
            tmdb_id=231620,
            show_name='现在就出发',
            sxx_episode=143,
            sxx_name='第4季 第2期下纯享·瞎掰故事王',
            title='老纯享',
            src_site_season=4,
            src_site_episode=9,
            subtype=None,           # 老版本没记类型
            content_key='',         # 也没有指纹
            status=STATUS_SUBMITTED,
        )
    )
    report = scan_show(
        show=SHOW,
        index=_index(),
        store=store,
        options=OPTIONS,
        search_fn=lambda name, sites: [FakeContext('Natural High S04E09 Plus 2026 2160p WEB-DL', '现在就出发 第四季 第05期 加更')],
        download_fn=lambda candidate: (True, 'hash'),
    )
    rec = next((r for r in report.records if 'Plus' in r['title']), None)
    assert rec is not None, [r['title'] for r in report.records]
    assert rec['status'] == '已提交', [r['status'] + ' ' + r['reason'] for r in report.records]
    assert rec['target'] == 'S00E174', rec


def check_period_mismatch_by_date() -> None:
    """站点期号与 TMDB 期号对不上、但同类型同一天播出：按日期认。

    站点把正片和衍生放在一套编号里数（第05期加更），TMDB 只给衍生单独编号
    （第3期加更），两边差几位是常态。只比期号的话，TMDB 明明有的资源会一直
    挂在「等待补齐」里。放宽到「同类型 + 同一天」才认，且必须日期确切。
    """
    index = SpecialIndex(
        tmdb_id=231620,
        show_name='现在就出发',
        episodes=[
            SpecialEpisode(episode=160, name='第4季 第3期加更', air_date='2026-11-08'),
            SpecialEpisode(episode=161, name='第4季 第3期花絮特辑', air_date='2026-11-08'),
            SpecialEpisode(episode=165, name='第4季 第4期加更', air_date='2026-11-15'),
        ],
        season_years={4: 2026},
        search_names=['Natural High'],
        fetched_at=time.time(),
    )
    parsed = parse_release('Natural High S04E09 Plus 2026 2160p WEB-DL', '现在就出发 第四季 第05期 加更 20261108')
    results = rank_specials(parsed, index)
    assert results, '期号对不上时没有退回按日期认'
    assert results[0].episode == 160, results[0].describe()
    assert any('按播出日期对齐' in line for line in results[0].reasons), results[0].reasons

    parsed = parse_release('Natural High S04E09 Plus 2026 2160p WEB-DL', '现在就出发 第四季 第05期 加更 20261109')
    assert not rank_specials(parsed, index), '日期不符也认了'


def main() -> int:
    checks = [
        ('跨节目类型识别', check_corpus),
        ('期号取自副标题', check_episode_from_desc),
        ('未收录写法保持未识别', check_unknown_has_no_subtype),
        ('未识别类型不落位', check_untyped_not_landed),
        ('未识别类型唯一位置可落位', check_untyped_landed_when_unambiguous),
        ('老记录无类型不误挡', check_unkeyed_without_subtype_not_blocking),
        ('期号不符按日期对齐', check_period_mismatch_by_date),
    ]
    failures: list[str] = []
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
    print(f'全部通过：{len(CORPUS)} 条跨节目命名 + {len(UNKNOWN)} 条未收录写法 + 4 项落位兜底')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
