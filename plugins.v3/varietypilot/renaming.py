"""把整理目标改写到特别季。

两级改写：
1. 渲染前改 rename_dict —— 让用户的命名模板自然渲染出 S00 与特别集名；
2. 渲染后改字符串 —— 兜底处理模板里硬编码的写法（Season 04 / S04E08）。

第二级是必需的：用户模板写法千差万别，只改字典无法覆盖
`{{season_fmt}}{{episode}}`、`第 {{season}} 季` 这类组合。
"""

from __future__ import annotations

import re

# 极端情况下集号可能超过 999，但综艺特别篇远达不到，仍按三位补齐
MAX_EPISODE_WIDTH = 3


def format_episode(episode: int) -> str:
    return f'{episode:02d}'


def format_season_episode(episode: int, season: int = 0) -> str:
    return f'S{season:02d}E{format_episode(episode)}'


_ILLEGAL_PATH_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_name(value: str) -> str:
    """清掉会破坏路径的字符。TMDB 集名里常见「：」和「/」。"""
    cleaned = _ILLEGAL_PATH_CHARS.sub(' ', value or '')
    return re.sub(r'\s+', ' ', cleaned).strip(' .')


def apply_to_rename_dict(rename_dict: dict, target_episode: int, target_title: str = '') -> dict:
    """把模板上下文改写到特别季第 target_episode 集。

    只改与季集相关的键，其余（标题、年份、技术字段）保持原样，
    这样「剧名 (年份)/Season 00/剧名 - S00E128 - 加更」能落在同一部剧目录下。
    """
    rename_dict['season'] = 0
    rename_dict['season_fmt'] = 'S00'
    rename_dict['season_episode'] = format_season_episode(target_episode)
    # 多集场景下 episode 可能是列表，这里统一收敛成单值
    rename_dict['episode'] = target_episode
    if target_title:
        # 集名会被渲染进文件名，必须去掉路径分隔符等非法字符
        rename_dict['episode_title'] = sanitize_name(target_title)
    return rename_dict


_RE_SEASON_EPISODE = re.compile(r'(?<![A-Za-z0-9])S(\d{1,2})\s*E(\d{1,4})(?!\d)', re.IGNORECASE)
_RE_SEASON_DIR = re.compile(r'(Season\s*)(\d{1,2})(?!\d)', re.IGNORECASE)
_RE_SEASON_CN_DIR = re.compile(r'(第\s*)(\d{1,2})(\s*季)')
_RE_SEASON_FMT = re.compile(r'(?<![A-Za-z0-9])S(\d{1,2})(?![A-Za-z0-9])')


def rewrite_render_str(render_str: str, target_episode: int) -> str:
    """改写已渲染的路径字符串，兜底处理模板里的硬编码季集写法。"""
    if not render_str:
        return render_str
    result = _RE_SEASON_EPISODE.sub(format_season_episode(target_episode), render_str)
    result = _RE_SEASON_DIR.sub(r'\g<1>00', result)
    result = _RE_SEASON_CN_DIR.sub(r'\g<1>0\g<3>', result)
    # 原串里没有 SxxExx 时，再处理孤立的季号写法（如文件名只写了 S04）
    if not _RE_SEASON_EPISODE.search(render_str):
        result = _RE_SEASON_FMT.sub('S00', result)
    return result


def ensure_special_dir(render_str: str, dir_name: str = 'Season 00') -> str:
    """确保路径里含特别季目录。

    极影视与 Emby 都按 `Season 00` 或 `Specials` 识别特别篇，
    但用户模板可能写成 `Season {{season}}` 后渲染成 `Season 0`，
    这里统一成两位写法。
    """
    if not render_str:
        return render_str
    if _RE_SEASON_DIR.search(render_str):
        return _RE_SEASON_DIR.sub(rf'\g<1>00', render_str)
    return render_str


def build_relative_path(show_dir: str, target_episode: int, show_name: str, episode_title: str, suffix: str = '') -> str:
    """生成标准的特别篇相对路径，供插件自行落位或页面预览使用。"""
    safe_title = re.sub(r'[\\/:*?"<>|]', ' ', episode_title or '').strip()
    safe_show = re.sub(r'[\\/:*?"<>|]', ' ', show_name or '').strip()
    name = f'{safe_show} - {format_season_episode(target_episode)}'
    if safe_title:
        name = f'{name} - {safe_title}'
    return f'{show_dir}/Season 00/{name}{suffix}'
