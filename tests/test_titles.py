"""标题解析器回归测试。样本取自 TMDB 真实 S00 标题与典型 PT 站点发布名。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins.v3' / 'varietypilot'))

from titles import detect_subtype, parse_title  # noqa: E402

# TMDB 真实 S00 集标题（现在就出发 / 我家那闺女）
TMDB_TITLES = [
    ('第1季 第1期彩蛋', 'bonus'),
    ('第1季 第1期加更', 'plus_bonus'),
    ('第2季 第1期超前加更', 'plus_bonus'),
    ('第2季 第2期加更上', 'plus_bonus'),
    ('第3季 第1期还有加更', 'plus_bonus'),
    ('第3季 特别加更', 'plus_bonus'),
    ('第3季 白敬亭直拍', 'clip'),
    ('第3季 空降直播', 'live'),
    ('第3季 先导片上', 'prequel'),
    ('第3季 名场面特辑 第1期上', 'special'),
    ('第二季 会员Plus版第1期', 'plus_bonus'),
    ('2025 加更版第10期：罗予彤《森林进化论》', 'plus_bonus'),
    ('2025 闺女轻加更第2期：罗予彤被评价生人勿近', 'plus_bonus'),
    ('2025 闺女超有料第10期：井迪余承恩带娃记', 'extra'),
    ('2026 闺女面对面第1期：陈妍希对话拉宏', 'extra'),
    ('2026 先导片 EP00上：闺女居家初展现', 'prequel'),
    ('2026 加更版第1期：陈瑶谢楠姐妹走心局', 'plus_bonus'),
    ('2026 闺女生活盲盒大放送第1期：陈瑶温柔养猫母带版', 'extra'),
    ('2026 超前营业第2期：陈瑶闺蜜约会日', 'extra'),
    ('2026 特别企划第1期：闺女黄灿灿BY5重聚', 'special'),
    ('2025 收官企划：闺女们返场表白', 'special'),
    ('2025 院人特辑上：罗予彤和少爷们', 'special'),
]

# 典型 PT 站点发布名
SITE_CASES = [
    # 站点 SxxExx 与真实期号不一致：以文本期号为准
    ('现在就出发.S04E08.第4期.加更.2160p.WEB-DL.H265.AAC-HHWEB',
     dict(season=4, episode=4, site_episode=8, subtype='plus_bonus')),
    ('现在就出发 第4季 第04期 彩蛋 1080p WEB-DL',
     dict(season=4, episode=4, subtype='bonus')),
    ('现在就出发.S04E10.第5期.纯享版.2160p.WEB-DL',
     dict(season=4, episode=5, subtype='pure')),
    ('我家那闺女2026.第03期.加更版.1080p.WEB-DL.H264',
     dict(episode=3, subtype='plus_bonus', year=2026)),
    ('我家那闺女2026 第5季 第3期 超前营业 2160p.WEB-DL',
     dict(season=5, episode=3, subtype='extra')),
    ('我家那闺女.2026.第10期.纯享.1080p',
     dict(episode=10, subtype='pure')),
    ('现在就出发.S04.2026.2160p.WEB-DL',
     dict(season=4, is_pack=True)),
    ('现在就出发.S04E05.第5期.2160p.WEB-DL',
     dict(season=4, episode=5, subtype=None)),
    ('我家那闺女2026.2026.10.25.第10期.加更.1080p',
     dict(episode=10, subtype='plus_bonus')),
    ('现在就出发.S04E02.第1期.上.加更.2160p',
     dict(season=4, episode=1, subtype='plus_bonus', part='上')),
]


def test_tmdb_subtypes() -> list[str]:
    failures = []
    for title, expect in TMDB_TITLES:
        got, _ = detect_subtype(title)
        if got != expect:
            failures.append(f'[TMDB] {title!r} 期望 {expect} 实际 {got}')
    return failures


def test_site_titles() -> list[str]:
    failures = []
    for title, expect in SITE_CASES:
        parsed = parse_title(title)
        for key, want in expect.items():
            got = getattr(parsed, key)
            if got != want:
                failures.append(f'[站点] {title!r} 字段 {key} 期望 {want!r} 实际 {got!r}')
    return failures


def main() -> int:
    failures = test_tmdb_subtypes() + test_site_titles()
    if failures:
        print(f'失败 {len(failures)} 项：')
        for line in failures:
            print('  -', line)
        return 1
    total = len(TMDB_TITLES) + len(SITE_CASES)
    print(f'全部通过：{total} 个用例')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
