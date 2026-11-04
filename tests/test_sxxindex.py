"""特别季匹配回归测试。

索引数据取自 TMDB 真实 S00 集名与播出日期（现在就出发 S3、我家那闺女 2026），
发布标题按 PT 站点常见写法构造。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins.v3'))

from varietypilot.sxxindex import (  # noqa: E402
    SpecialEpisode,
    SpecialIndex,
    match_for_title,
    next_free_episode,
)

# 现在就出发 S3 特别篇（真实数据）
JXCF_ROWS = [
    (53, '第3季 先导片上', '2025-10-18'),
    (54, '第3季 先导片下', '2025-10-19'),
    (55, '第3季 空降直播', '2025-10-19'),
    (56, '第3季 空降直播', '2025-10-26'),
    (57, '第3季 第1期加更', '2025-10-27'),
    (58, '第3季 空降直播', '2025-10-27'),
    (59, '第3季 第1期还有加更', '2025-10-28'),
    (60, '第3季 白敬亭直拍', '2025-10-29'),
    (64, '第3季 第2期加更', '2025-11-03'),
    (65, '第3季 第2期还有加更', '2025-11-04'),
    (71, '第3季 第3期加更', '2025-11-10'),
    (72, '第3季 第3期还有加更', '2025-11-11'),
    (77, '第3季 第4期加更', '2025-11-17'),
    (89, '第3季 特别加更', '2025-11-27'),
]

# 我家那闺女 2026（S5）特别篇（真实数据）
WJNG_ROWS = [
    (92, '2026 先导片 EP00上：闺女居家初展现', '2026-08-23'),
    (93, '2026 先导片 EP00下：闺女好友排序', '2026-08-23'),
    (94, '2026 特别企划第1期：闺女黄灿灿BY5重聚', '2026-08-23'),
    (95, '2026 超前营业第1期：张月黄灿灿互飙情话', '2026-08-29'),
    (97, '2026 加更版第1期：陈瑶谢楠姐妹走心局', '2026-08-31'),
    (98, '2026 闺女生活盲盒大放送第1期：陈瑶温柔养猫母带版', '2026-09-02'),
    (100, '2026 超前营业第2期：陈瑶闺蜜约会日', '2026-09-05'),
    (101, '2026 加更版第2期：黄灿灿罗予彤天台约定', '2026-09-07'),
    (104, '2026 超前营业第3期：陈妍希许愿想要女儿', '2026-09-12'),
    (105, '2026 加更版第3期：陈瑶的爱永远拿得出手', '2026-09-14'),
]

JXCF = SpecialIndex(
    tmdb_id=231620,
    show_name='现在就出发',
    season_years={0: 2023, 1: 2023, 2: 2024, 3: 2025, 4: 2026},
)
JXCF.episodes = [SpecialEpisode(episode=e, name=n, air_date=d) for e, n, d in JXCF_ROWS]

WJNG = SpecialIndex(
    tmdb_id=126490,
    show_name='我家那闺女',
    season_years={0: 2020, 1: 2019, 2: 2020, 3: 2024, 4: 2025, 5: 2026},
)
WJNG.episodes = [SpecialEpisode(episode=e, name=n, air_date=d) for e, n, d in WJNG_ROWS]

CASES = [
    # (剧, 站点发布名, 期望集号)
    (JXCF, '现在就出发.S03E08.第1期.加更.2160p.WEB-DL.H265.AAC-HHWEB', 57),
    (JXCF, '现在就出发.S03E09.第1期.还有加更.2160p.WEB-DL', 59),
    (JXCF, '现在就出发.S03E10.第2期.加更.2160p.WEB-DL', 64),
    (JXCF, '现在就出发.S03E11.第2期.还有加更.1080p', 65),
    (JXCF, '现在就出发 第3季 第3期 加更 2160p WEB-DL', 71),
    (JXCF, '现在就出发.S03.先导片上.1080p.WEB-DL', 53),
    (JXCF, '现在就出发第3季空降直播20251026.1080p', 56),
    (WJNG, '我家那闺女2026.第2期.加更版.1080p.WEB-DL.H264', 101),
    (WJNG, '我家那闺女2026.第3期.加更版.2160p.WEB-DL', 105),
    (WJNG, '我家那闺女2026.第3期.超前营业.1080p', 104),
    (WJNG, '我家那闺女2026 第1期 超前营业 2160p', 95),
    (WJNG, '我家那闺女2026.第1期.特别企划.1080p', 94),
    # 应匹配不到：正片第5期在特别篇里没有对应登记
    (JXCF, '现在就出发.S03E05.第5期.2160p.WEB-DL', None),
    (JXCF, '现在就出发.S03E01.第1期.2160p.WEB-DL', None),
    (JXCF, '现在就出发.S03.2025.2160p.WEB-DL', None),
    (WJNG, '我家那闺女2026.第9期.1080p.WEB-DL', None),
]


def main() -> int:
    failures = []
    for index, title, expect in CASES:
        names = [index.show_name]
        result, parsed = match_for_title(title, names, index)
        got = result.episode if result else None
        if got != expect:
            detail = result.describe() if result else '无匹配'
            failures.append(
                f'{title!r}\n      期望 {expect} 实际 {got}｜{detail}\n      解析：季={parsed.season} 期={parsed.episode} 类型={parsed.subtype} 修饰={parsed.modifier}'
            )

    # 尾部空号
    tail = next_free_episode(JXCF)
    if tail != 90:
        failures.append(f'尾部空号期望 90 实际 {tail}')

    if failures:
        print(f'失败 {len(failures)} 项：')
        for line in failures:
            print('  -', line)
        return 1
    print(f'全部通过：{len(CASES)} 个匹配用例 + 尾部空号')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
