"""未被接管的衍生文件兜底改写测试。

场景：用户手动下载了加更，插件里没有对应任务。
若不干预，MP 会按站点编号落位，可能正好压在正片集号上，
再叠加「总是覆盖」策略就会把正片顶掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'plugins.v3'))

from varietypilot import VarietyPilot  # noqa: E402
from varietypilot.renaming import rewrite_render_str  # noqa: E402


def _dict(original: str, season: int = 4, episode: int = 8) -> dict:
    return {
        'original_name': original,
        'season': season,
        'season_fmt': 'S04',
        'episode': episode,
        'season_episode': f'S04E{episode:02d}',
        'name': '现在就出发',
    }


def check(label: str, fn) -> None:
    try:
        fn()
        print(f'  通过：{label}')
    except AssertionError as err:
        print(f'  失败：{label} —— {err}')
        raise SystemExit(1)


def test_target_numbering() -> None:
    plugin = VarietyPilot()
    target = plugin._guard_target(_dict('Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb'))
    assert target is not None, '应能识别为衍生内容'
    episode, title = target
    assert episode == 908, f'兜底集号应在 9xx 段，实际 {episode}'
    assert title.startswith('第4季'), title


def test_positive_episode_uses_real_period() -> None:
    plugin = VarietyPilot()
    parsed_dict = _dict('Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb')
    parsed_dict['original_name'] = 'Natural High S04E08 Plus'
    target = plugin._guard_target(parsed_dict)
    assert target and target[0] == 908, target


def test_plain_episode_untouched() -> None:
    plugin = VarietyPilot()
    assert plugin._guard_target(_dict('Natural High S04E08 2026 1080p TX WEB-DL H265 AAC-ADWeb')) is None


def test_switch_off_by_default() -> None:
    plugin = VarietyPilot()
    data = _dict('Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb')
    plugin._guard_unmatched(data)
    assert data['season'] == 4 and data['episode'] == 8, '默认关闭时不应改动'


def test_switch_on_rewrites() -> None:
    plugin = VarietyPilot()
    plugin._config['guard_unmatched'] = True
    data = _dict('Natural High S04E08 Plus 2026 1080p TX WEB-DL H265 AAC-ADWeb')
    plugin._guard_unmatched(data)
    assert data['season'] == 0, data
    assert data['season_episode'] == 'S00E908', data
    assert '加更' in str(data.get('episode_title')), data


def test_render_string_fallback() -> None:
    text = rewrite_render_str('现在就出发/Season 04/现在就出发 - S04E08 - 加更.mkv', 908)
    assert 'S00E908' in text and 'Season 00' in text, text


def main() -> None:
    check('兜底集号落在 9xx 段', test_target_numbering)
    check('站点编号参与兜底编号', test_positive_episode_uses_real_period)
    check('正片不参与兜底', test_plain_episode_untouched)
    check('开关默认关闭', test_switch_off_by_default)
    check('开启后改写为 S00', test_switch_on_rewrites)
    check('已渲染路径兜底改写', test_render_string_fallback)
    print('全部通过：未接管资源兜底')


if __name__ == '__main__':
    main()
