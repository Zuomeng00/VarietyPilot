"""三张站点截图（2026-11-20）的完整落位验证。

语料见 corpus_site_1120.py，按截图原文录入，未做改写。
覆盖的坑：

1. 正片带季号+期号+分P+片段名，曾经过关落进同期的纯享集；
2. 整季包不能进衍生链路；
3. 分P 写成「第11期（上）」时，括号挡住分P 解析，同期上下两集算成同一指纹；
4. 站点的「加码 / Extra」「沉浸版 / Immersive」以前认不出类型，会当无类型放行；
5. 年度季号 `S2026E26` + 「抢鲜逛第25期」这类期号与站点号错位；
6. 同一剧同一期有特辑/加更/还有加更/多条纯享，站点序号还会复用。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from varietypilot.pipeline import Options, TrackedShow, scan_show  # noqa: E402
from varietypilot.sxxindex import SpecialEpisode, SpecialIndex  # noqa: E402
from varietypilot.store import TaskStore  # noqa: E402
from varietypilot.titles import detect_subtype, parse_release, strip_show_name  # noqa: E402
from corpus_site_1120 import CORPUS  # noqa: E402


class FakeTorrent:
    def __init__(self, title, description='', size=6 * 1024 ** 3, seeders=12,
                 factor=1.0, site='观众'):
        self.title = title
        self.description = description
        self.site_name = site
        self.size = size
        self.seeders = seeders
        self.downloadvolumefactor = factor


class FakeContext:
    def __init__(self, title, description='', **kwargs):
        self.torrent_info = FakeTorrent(title, description, **kwargs)


SHOWS = {
    '现在就出发': dict(
        tmdb_id=231620, alias='Natural High', seasons=[4], year=2026,
        episodes=[
            SpecialEpisode(episode=178, name='第4季 第6期上纯享·笨蛋杀手', air_date='2026-11-16'),
            SpecialEpisode(episode=179, name='第4季 第6期上纯享·脑筋猜题', air_date='2026-11-16'),
            SpecialEpisode(episode=180, name='第4季 第6期下纯享·潜伏狼人杀', air_date='2026-11-17'),
            SpecialEpisode(episode=181, name='第4季 第6期加更', air_date='2026-11-16'),
            SpecialEpisode(episode=182, name='第4季 第6期还有加更', air_date='2026-11-17'),
            SpecialEpisode(episode=183, name='第4季 第6期花絮特辑', air_date='2026-11-18'),
        ],
        expect={'花絮特辑': 183, '还有加更': 182, '加更': 181,
                '纯享06下': 180, '纯享06上A': 178, '纯享06上B': 179},
    ),
    '后会友期': dict(
        tmdb_id=900001, alias='I Miss You', seasons=[1], year=2026,
        episodes=[
            SpecialEpisode(episode=20, name='第1季 第5期抢鲜版', air_date='2026-11-10'),
            SpecialEpisode(episode=21, name='第1季 第5期加更', air_date='2026-11-10'),
            SpecialEpisode(episode=22, name='第1季 第6期上沉浸版', air_date='2026-11-17'),
            SpecialEpisode(episode=23, name='第1季 第6期下沉浸版', air_date='2026-11-18'),
        ],
        expect={'抢鲜版': 20, '加更版': 21, '沉浸版上': 22, '沉浸版下': 23},
    ),
    '偏爱之恋': dict(
        tmdb_id=900002, alias='Love Wins All', seasons=[2], year=2026,
        episodes=[
            SpecialEpisode(episode=1, name='第2季 直播', air_date='2026-11-12'),
            SpecialEpisode(episode=2, name='第2季 加码', air_date='2026-11-13'),
        ],
        expect={'直播': 1, '加码': 2},
    ),
    '你好，星期六': dict(
        tmdb_id=900003, alias='Hi6', seasons=[2026], year=2026,
        episodes=[
            SpecialEpisode(episode=26, name='第2026季 抢鲜逛 第25期', air_date='2026-11-18'),
        ],
        expect={'抢鲜逛': 26},
    ),
    '花儿与少年': dict(
        tmdb_id=900004, alias='Divas Hit the Road', seasons=[8], year=2026,
        episodes=[SpecialEpisode(episode=17, name='第8季 第11期上', air_date='2026-11-18')],
        expect={},
    ),
    '声生不息': dict(
        tmdb_id=900005, alias='Infinity and Beyond', seasons=[6], year=2026,
        episodes=[SpecialEpisode(episode=2, name='第6季 第1期下', air_date='2026-11-18')],
        expect={},
    ),
    '热浪之外': dict(
        tmdb_id=900006, alias='Beyond Trending', seasons=[3], year=2026,
        episodes=[SpecialEpisode(episode=5, name='第3季 第5期', air_date='2026-11-17')],
        expect={},
    ),
    '继续营业中': dict(
        tmdb_id=900007, alias='Loud Eyes', seasons=[1], year=2026,
        episodes=[SpecialEpisode(episode=1, name='第1季 第1期', air_date='2026-11-18')],
        expect={},
    ),
    '国宝寻音': dict(
        tmdb_id=900008, alias='Treasures in Tune', seasons=[1], year=2026,
        episodes=[SpecialEpisode(episode=3, name='第1季 第2期', air_date='2026-11-19')],
        expect={},
    ),
    '爱情保卫战': dict(
        tmdb_id=900009, alias='Ai Qing Bao Wei Zhan', seasons=[2026], year=2026,
        episodes=[SpecialEpisode(episode=114, name='第2026季 第114期', air_date='2026-11-18')],
        expect={},
    ),
    '舞蹈新风暴': dict(
        tmdb_id=900010, alias='Dance Smash', seasons=[1], year=2026,
        episodes=[SpecialEpisode(episode=1, name='第1季 第1期', air_date='2026-11-01')],
        expect={},
    ),
    '合伙人': dict(
        tmdb_id=900011, alias='The Chinese Beauty Shop', seasons=[3], year=2026,
        episodes=[SpecialEpisode(episode=1, name='第3季 第1期', air_date='2026-11-01')],
        expect={},
    ),
}

# 用户把九类衍生全部关掉时的选择
ALL_OFF = ['clip', 'uncut', 'pure', 'special', 'live', 'extra', 'bonus', 'plus_bonus', 'prequel']


def contexts_of(show_name):
    return [
        FakeContext(title, desc)
        for cn, _en, title, desc, _note in CORPUS
        if cn == show_name
    ]


def run(show_name, meta, skip):
    index = SpecialIndex(
        tmdb_id=meta['tmdb_id'],
        show_name=show_name,
        episodes=meta['episodes'],
        season_years={meta['seasons'][0]: meta['year']},
        search_names=[show_name, meta['alias']],
        fetched_at=0,
    )
    show = TrackedShow(name=show_name, tmdb_id=meta['tmdb_id'],
                       aliases=[meta['alias']], seasons=meta['seasons'])
    submitted = []

    def download(candidate):
        submitted.append(candidate)
        return True, 'hash'

    tmp = tempfile.TemporaryDirectory()
    store = TaskStore(Path(tmp.name) / 'tasks.json')
    report = scan_show(
        show=show, index=index, store=store,
        options=Options(min_seeders=1, min_size_gb=0.05, max_size_gb=20.0,
                        prefer_resolution='2160p', skip_subtypes=skip),
        search_fn=lambda name, sites: contexts_of(show_name),
        download_fn=download,
    )
    store._tmp = tmp
    return submitted, report


def check(label, fn):
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


# ---------------------------------------------------------------- 解析层

def test_bracket_part() -> None:
    """「第11期（上）」的括号写法必须解析出分P，否则上下两集同指纹。"""
    cases = [
        ('花儿与少年 第八季 正片 第11期（上）：全员体验狗拉雪橇', '上'),
        ('声生不息 第六季 正片 第01期（下）：刘耀文演绎千禧金曲', '下'),
        ('后会友期 第一季 沉浸版 第06期（上）：心愿完成！', '上'),
        ('后会友期 第一季 沉浸版 第06期（下）：三痛哭！', '下'),
    ]
    for text, want in cases:
        parsed = parse_release(text)
        assert parsed.part == want, f'{text} -> part={parsed.part}，期望 {want}'


def test_new_type_words() -> None:
    """截图里的新叫法都要认得出类型，不能当无类型放行。"""
    cases = {
        '加码': 'plus_bonus',
        '偏爱加码': 'plus_bonus',
        'Extra': 'plus_bonus',
        '沉浸版': 'pure',
        'Immersive': 'pure',
        'Preview': 'prequel',
        'Peek': 'prequel',
        '抢鲜版': 'prequel',
        '抢鲜逛': 'prequel',
    }
    for word, want in cases.items():
        got, _alias = detect_subtype(word)
        assert got == want, f'{word} -> {got}，期望 {want}'


def test_immersive_has_part_and_type() -> None:
    """沉浸版上下两条：类型=纯享，分P 要分开，否则会撞同一集。"""
    names = ['后会友期', 'I Miss You']
    upper = parse_release(strip_show_name(
        'I Miss You S01E09 Immersive 2026 1080p MGTV WEB-DL H264 AAC-ADWeb', names),
        strip_show_name('后会友期 第一季 沉浸版 第06期（上）：心愿完成！', names))
    lower = parse_release(strip_show_name(
        'I Miss You S01E10 Immersive 2026 1080p MGTV WEB-DL H264 AAC-ADWeb', names),
        strip_show_name('后会友期 第一季 沉浸版 第06期（下）：三痛哭！', names))
    assert upper.subtype == 'pure' and lower.subtype == 'pure', (upper.subtype, lower.subtype)
    assert upper.part == '上' and lower.part == '下', (upper.part, lower.part)
    assert upper.episode == lower.episode == 6, (upper.episode, lower.episode)


def test_segment_cuts_alias_tail() -> None:
    """副标题末尾的「| 别名 / 标签」不能算进片段名。

    「…惊喜合唱 | 声生不息·宝岛季2 / 声生不息2026」这类尾巴一旦进指纹，
    同一条资源换个站点写法就是另一份内容，会被重复下载。
    """
    names = ['声生不息', 'Infinity and Beyond']
    title = 'Infinity and Beyond S06E02 2026 2160p MGTV WEB-DL H264 AAC-ADWeb'
    base = '刘耀文演绎千禧金曲 陈楚生曾沛慈惊喜合唱'
    with_alias = parse_release(
        strip_show_name(title, names),
        strip_show_name(f'声生不息 第六季 正片 第01期（下）：{base} | 声生不息·宝岛季2 / 声生不息2026', names))
    without = parse_release(
        strip_show_name(title, names),
        strip_show_name(f'声生不息 第六季 正片 第01期（下）：{base}', names))
    assert with_alias.segment == base, with_alias.segment
    assert with_alias.segment == without.segment, (with_alias.segment, without.segment)


def test_feature_and_pack_never_enter() -> None:
    """正片与整季包一条都不能进候选。"""
    for _cn, en, title, desc, note in CORPUS:
        names = [_cn, en]
        parsed = parse_release(strip_show_name(title, names), strip_show_name(desc, names))
        if '正片' in desc:
            assert parsed.is_feature, f'正片没被标出：{note} / {desc[:30]}'
        if '整季包' in note:
            assert parsed.is_pack, f'整季包没被标出：{note} / {desc[:30]}'


# ---------------------------------------------------------------- 落位层

def test_now_going_lands_each_episode() -> None:
    """现在就出发：六条衍生各落一格，且不能占正片/其他类型的集。"""
    meta = SHOWS['现在就出发']
    submitted, report = run('现在就出发', meta, skip=[])
    got = sorted(cand.match.episode for cand in submitted)
    assert got == sorted(meta['expect'].values()), got
    assert not any(cand.parsed.is_feature for cand in submitted), '正片被提交了'
    # 一条资源只落一个集号
    assert len(set(got)) == len(got), f'有资源撞到同一集：{got}'


def test_i_miss_you_lands_each_episode() -> None:
    """后会友期：抢鲜版/加更/沉浸版上下各落对集。"""
    meta = SHOWS['后会友期']
    submitted, report = run('后会友期', meta, skip=[])
    got = sorted(cand.match.episode for cand in submitted)
    assert got == sorted(meta['expect'].values()), got


def test_love_wins_all_live_and_extra() -> None:
    """偏爱之恋：直播与加码各落一格，加码不能被当成正片漏掉。"""
    submitted, _report = run('偏爱之恋', SHOWS['偏爱之恋'], skip=[])
    pairs = sorted((cand.parsed.subtype, cand.match.episode) for cand in submitted)
    assert pairs == [('live', 1), ('plus_bonus', 2)], pairs


def test_hi6_year_season_peek() -> None:
    """你好，星期六：年度季号 + 抢鲜逛第25期，落到站点号 E26 对应的集。"""
    submitted, _report = run('你好，星期六', SHOWS['你好，星期六'], skip=[])
    assert len(submitted) == 1, submitted
    assert submitted[0].match.episode == 26, submitted[0].match.episode


def test_feature_only_shows_submit_nothing() -> None:
    """只有正片的几部：花少、声生不息、热浪之外、继续营业中、国宝寻音、爱情保卫战。"""
    for name in ('花儿与少年', '声生不息', '热浪之外', '继续营业中', '国宝寻音', '爱情保卫战'):
        submitted, _report = run(name, SHOWS[name], skip=[])
        assert submitted == [], f'{name} 下了 {[c.title for c in submitted]}'


def test_pack_only_shows_submit_nothing() -> None:
    """只有整季包的两部：舞蹈新风暴、合伙人。"""
    for name in ('舞蹈新风暴', '合伙人'):
        submitted, _report = run(name, SHOWS[name], skip=[])
        assert submitted == [], f'{name} 下了 {[c.title for c in submitted]}'


def test_all_types_off_submits_nothing() -> None:
    """九类衍生全关：全语料一条都不该提交。"""
    for name, meta in SHOWS.items():
        submitted, _report = run(name, meta, skip=ALL_OFF)
        assert submitted == [], f'{name} 关掉全部衍生类型后仍下了 {[c.title for c in submitted]}'


def test_pure_off_also_blocks_immersive() -> None:
    """关「纯享」时，归在纯享里的「沉浸版」一起挡掉，抢鲜版/加更照常。"""
    submitted, report = run('后会友期', SHOWS['后会友期'], skip=['pure'])
    subtypes = sorted(cand.parsed.subtype for cand in submitted)
    assert subtypes == ['plus_bonus', 'prequel'], subtypes
    targets = {cand.match.episode for cand in submitted}
    assert 22 not in targets and 23 not in targets, targets
    lines = ' '.join(report.details)
    assert '已按设置跳过「纯享」2 条' in lines, report.details


def test_plus_off_blocks_jia_ma() -> None:
    """关「加更」时，「加码」同属加更，一起挡；直播照常。"""
    submitted, _report = run('偏爱之恋', SHOWS['偏爱之恋'], skip=['plus_bonus'])
    assert [cand.parsed.subtype for cand in submitted] == ['live'], submitted


if __name__ == '__main__':
    check('括号写法的分P', test_bracket_part)
    check('新类型词识别', test_new_type_words)
    check('沉浸版类型与分P', test_immersive_has_part_and_type)
    check('片段名切掉别名尾巴', test_segment_cuts_alias_tail)
    check('正片与整季包不进链路', test_feature_and_pack_never_enter)
    check('现在就出发六条各落一格', test_now_going_lands_each_episode)
    check('后会友期四条各落一格', test_i_miss_you_lands_each_episode)
    check('偏爱之恋直播与加码', test_love_wins_all_live_and_extra)
    check('你好星期六年度季号抢鲜逛', test_hi6_year_season_peek)
    check('纯正片综艺不下', test_feature_only_shows_submit_nothing)
    check('纯整季包综艺不下', test_pack_only_shows_submit_nothing)
    check('九类全关一条不下', test_all_types_off_submits_nothing)
    check('关纯享连带沉浸版', test_pure_off_also_blocks_immersive)
    check('关加更连带加码', test_plus_off_blocks_jia_ma)
    print('全部通过：三张站点截图语料')
