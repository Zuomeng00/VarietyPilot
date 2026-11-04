"""订阅发现与综艺类型判定的回归测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins.v3'))

from varietypilot.subscribes import (  # noqa: E402
    SubscriptionItem,
    VarietyCache,
    collect,
    is_variety_details,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    if not condition:
        failures.append(f'{name} 失败 {detail}')


def make(tmdb_id: int, name: str, season=None, source='themoviedb', year='2026', mtype='电视剧'):
    return {
        'media_source': source,
        'media_id': str(tmdb_id),
        'name': name,
        'season': season,
        'year': year,
        'type': mtype,
    }


# ---- 类型判定
check('真人秀是综艺', is_variety_details({'genres': [{'id': 10764, 'name': '真人秀'}]}))
check('脱口秀是综艺', is_variety_details({'genres': [{'id': 10767, 'name': '脱口秀'}]}))
check('剧情片不是综艺', not is_variety_details({'genres': [{'id': 18, 'name': '剧情'}]}))
check('无类型不误判', not is_variety_details({'genres': []}))

# ---- 筛选与去重
items = [
    make(231620, '现在就出发', season=4),
    make(231620, '现在就出发', season=3),
    make(126490, '我家那闺女', season=5),
    make(999, '某部美剧', season=1),
    make(888, '豆瓣条目', season=1, source='douban'),
]
variety = {231620: True, 126490: True, 999: False}
shows, skipped = collect(items, is_variety=lambda i: variety.get(i), scope='variety')
names = [row.name for row in shows]
check('只留综艺', sorted(names) == ['我家那闺女', '现在就出发'], str(names))
check('同剧合并季号', sorted(next(r for r in shows if r.tmdb_id == 231620).seasons) == [3, 4])
check('非 TMDB 来源跳过', all(row.tmdb_id != 888 for row in shows))

# ---- 排除名单
shows, _ = collect(items, is_variety=lambda i: True, exclude_ids=[231620], scope='all')
check('排除生效', all(row.tmdb_id != 231620 for row in shows))
check('范围 all 保留非综艺', any(row.tmdb_id == 999 for row in shows))

# ---- 类型未知
shows, skipped = collect(items, is_variety=lambda i: None if i == 999 else True)
check('类型未知跳过', all(row.tmdb_id != 999 for row in shows))
check('类型未知有说明', any('999' in item for item in skipped), str(skipped))

# ---- 缓存读写
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'genre_cache.json'
    cache = VarietyCache(path)
    check('未缓存返回 None', cache.get(231620) is None)
    cache.set(231620, True)
    cache.set(18, False)
    cache.save()
    again = VarietyCache(path)
    check('缓存命中', again.get(231620) is True)
    check('负结果也缓存', again.get(18) is False)

# ---- 手动优先
from varietypilot.pipeline import TrackedShow  # noqa: E402
import varietypilot as vp  # noqa: E402

plugin = vp.VarietyPilot()
plugin._manual_shows = [TrackedShow(name='现在就出发', tmdb_id=231620, year='2023')]
plugin._auto_shows = [
    TrackedShow(name='现在就出发', tmdb_id=231620),
    TrackedShow(name='我家那闺女', tmdb_id=126490),
]
plugin._rebuild_shows()
merged = {show.tmdb_id: show for show in plugin._shows}
check('合并后两部', len(merged) == 2, str(list(merged)))
check('手动优先', merged[231620].year == '2023', merged[231620].year)

# ---- 站点配置两种形态
check('数组站点', plugin._parse_site_ids([1, 3, 3]) == [1, 3])
check('字符串站点', plugin._parse_site_ids('1,3，5') == [1, 3, 5])
check('空站点', plugin._parse_site_ids('') == [])

if failures:
    print('\n'.join(failures))
    raise SystemExit(1)
print('订阅同步测试全部通过')
