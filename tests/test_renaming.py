"""重命名改写回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins.v3'))

from varietypilot.renaming import apply_to_rename_dict, format_season_episode, rewrite_render_str  # noqa: E402

CASES = [
    # (原渲染路径, 目标集号, 期望)
    ('现在就出发 (2023)/Season 04/现在就出发 - S04E08 - 第4期加更',
     128, '现在就出发 (2023)/Season 00/现在就出发 - S00E128 - 第4期加更'),
    ('现在就出发/Season 4/现在就出发 S4E8',
     128, '现在就出发/Season 00/现在就出发 S00E128'),
    ('我家那闺女 (2019)/Season 05/我家那闺女 - S05E03',
     145, '我家那闺女 (2019)/Season 00/我家那闺女 - S00E145'),
    ('剧名 (2026)/Season 04/剧名 第 4 季 第 8 期',
     100, '剧名 (2026)/Season 00/剧名 第 0 季 第 8 期'),
    # 只有季号、没有集号的场景
    ('剧名/Season 04/剧名 S04',
     120, '剧名/Season 00/剧名 S00'),
    # MP 默认模板渲染结果（Season {{season}} 会渲染成 Season 0）
    ('现在就出发 (2023)/Season 0/现在就出发 - S04E08 - 第 8 集.mkv',
     113, '现在就出发 (2023)/Season 00/现在就出发 - S00E113 - 第 8 集.mkv'),
]


def main() -> int:
    failures = []
    for raw, episode, expect in CASES:
        got = rewrite_render_str(raw, episode)
        if got != expect:
            failures.append(f'{raw!r}\n      期望 {expect!r}\n      实际 {got!r}')

    ctx = {'season': 4, 'season_fmt': 'S04', 'episode': 8, 'season_episode': 'S04E08', 'episode_title': '第4期'}
    apply_to_rename_dict(ctx, 128, '第4期加更')
    expect_ctx = {
        'season': 0, 'season_fmt': 'S00', 'episode': 128,
        'season_episode': 'S00E128', 'episode_title': '第4期加更',
    }
    for key, want in expect_ctx.items():
        if ctx.get(key) != want:
            failures.append(f'字典字段 {key} 期望 {want!r} 实际 {ctx.get(key)!r}')

    if format_season_episode(5) != 'S00E05':
        failures.append(f'集号格式错误：{format_season_episode(5)}')

    # TMDB 集名里的路径分隔符与西文非法字符不能进路径（中文全角标点保留）
    dirty = {'season': 4, 'episode': 8}
    apply_to_rename_dict(dirty, 113, '第4季 第4期还有加更：白敬亭/林一')
    if any(ch in dirty['episode_title'] for ch in '/\\:*?"<>|'):
        failures.append(f'集名未清洗：{dirty["episode_title"]!r}')

    if failures:
        print(f'失败 {len(failures)} 项：')
        for line in failures:
            print('  -', line)
        return 1
    print(f'全部通过：{len(CASES)} 个路径用例 + 字典改写')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
