"""综艺标题解析：把站点发布名拆成「季 / 期号 / 衍生类型 / 分P」等结构化信号。

设计原则：
1. 类型词表按别名长度倒序匹配，保证「超前加更」不会被「加更」抢先命中。
2. 期号、季号、日期三路信号独立提取，互不覆盖，留给匹配器按置信度组合。
3. 解析只做「读出什么」，不做「应该是什么」，任何推断都由 matcher 负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# ---------------------------------------------------------------- 类型词表

# 规范类型 -> 别名。别名越长越先匹配。
#
# 国内综艺的衍生物料实际是「一份录制素材拆成七八个版本」卖会员，
# 各平台叫法互不统一，这里按内容性质归成八类：
#   加更   —— 同一期素材的二次放出（会员Plus版、加长版、悠享版都算）
#   纯享   —— 剪掉其他嘉宾、只留看点的精简版
#   彩蛋   —— 正片之外单独剪的小段，通常几分钟
#   花絮   —— 幕后、NG、未播片段这类边角料
#   直拍   —— 单人机位全程跟拍
#   先导   —— 正片之前的预告、抢先看
#   特辑   —— 特别企划、专场这类整期新内容
#   直播   —— 陪看、见面会、开播直播夜
#   衍生   —— 独立成节目的衍生番（超有料、营业中这类）
SUBTYPE_ALIASES: dict[str, tuple[str, ...]] = {
    'plus_bonus': (
        # 会员版家族：正片 + 未播内容，本质是加长
        '会员plus版', '会员PLUS版', '会员Plus版', 'plus版', 'PLUS版', 'Plus版',
        'VIP加长版', 'vip加长版', 'svip加长版', 'SVIP加长版', '会员加长版',
        '超长版', '加长版', '悠享版', '会员版', '尊享版', 'SVIP版', 'svip版',
        'VIP版', 'vip版',
        # 加更家族
        '会员加更版', '会员加更', '特别加更', '超前加更', '还有加更', '再次加更',
        '二次加更', '再加更', '又加更', '超长加更', '轻加更', '加料版',
        '加更版', '加更',
        # 「加码」是爱奇艺/腾讯对加更的叫法（偏爱之恋 第二季 加码：…），
        # 站点资源名写成 `S02E02 Extra`。两边都按加更处理，才能和 TMDB 对上。
        '加码版', '加码',
        # 未删减 / 完整版：同一期素材的完整放出，性质上就是加长
        '未删减版', '无删减版', '未删减', '无删减', '会员完整版', '完整版',
        '导演剪辑版', '加映版', '加映',
        # 站点资源名常直接写英文 Plus，如 `Natural High S04E08 Plus`
        'plus', 'uncensored', 'extended',
        # `Love Wins All S02E02 Extra`：站点用 Extra 标额外放出的内容
        'extra',
    ),
    'pure': (
        '单人纯享', '嘉宾纯享', '看点纯享', '纯享版', '纯享',
        '精编版', '精华版', '高光时刻', '高光', '精华',
        # 芒果「沉浸版」是把正片重剪成沉浸式的一个版本，性质和纯享一样，
        # 站点资源名写 `I Miss You S01E09 Immersive`。
        # 只认「沉浸版」不认裸「沉浸」，避免「沉浸式体验」这类描述被当成类型词。
        '沉浸版', 'immersive',
    ),
    'bonus': (
        'VIP彩蛋', 'vip彩蛋', '独家彩蛋', '精彩彩蛋', '彩蛋版', '彩蛋篇',
        '彩蛋', '番外篇', '番外',
        # 站点资源名直接写英文 Eggs，如 `Natural High S04E01 Eggs`
        'eggs', 'egg',
    ),
    'uncut': (
        '超长花絮', '拍摄花絮', '幕后花絮', '未播花絮', '会员花絮', '花絮集锦',
        '花絮合集', '未播集锦', '花絮',
        '未播片段', '未播内容', '未播', '母带放送', '母带',
        'NG镜头', 'NG片段', 'ng镜头', 'ng片段', '幕后', '探班', '全纪实',
        'behind the scenes', 'behind scenes', 'bts',
    ),
    'prequel': (
        '开播先导', '先导片上', '先导片下', '先导片', '先导集', '先导',
        '预告片', '下期预告', '高能预警', '抢鲜看', '抢先看', '抢鲜', '预告',
        '序章', '前瞻', '预热',
        # 站点资源名直接写英文 Prologue / Prelude / Preview / Peek
        'prologue', 'prelude', 'preview', 'peek',
    ),
    'special': (
        '名场面特辑', '游戏特辑', '院人特辑', '特别企划', '复盘特辑', '案情回顾',
        '特辑', '特别篇', '特别版', '特别节目', '企划', '专场', '复盘', '回顾', '特别',
        # 站点资源名常直接写英文 Special，如 `Mao Xue Woof S01E02 Special`
        'special',
    ),
    'live': (
        '开播直播夜', '收官直播', '季中见面会', '直播回看', '空降直播',
        '慢直播', '看片会', '见面会', '陪看', '直播',
    ),
    'clip': (
        '精彩直拍', '单人直拍', '个人直拍', '嘉宾直拍', '竖屏直拍', '横屏直拍',
        '第一视角', '单机位', '个人cut', '个人CUT', '直拍版', '直拍',
        # 站点常用英文 Fancam 标直拍
        'fancam', 'fan cam', 'focus cam',
        'cut', 'CUT',
    ),
    'extra': (
        '闺女生活盲盒大放送', '生活盲盒大放送', '盲盒大放送', '盲盒',
        '闺女超有料', '超有料',
        '超前营业', '超前营夜', '营业中', '营夜', '营业',
        '音乐人来了', '面对面', '专访', '访谈', '圆桌',
        '会员专享', '专享', '衍生',
    ),
}

# 类型的中文短标签：站点资源名常写英文 Plus/Pure/Special，
# 落位名和明细里统一显示中文，避免生成「第4季 第8期plus」这种半截英文文件名。
SUBTYPE_LABELS: dict[str, str] = {
    'plus_bonus': '加更',
    'pure': '纯享',
    'bonus': '彩蛋',
    'uncut': '花絮',
    'clip': '直拍',
    'prequel': '先导片',
    'special': '特辑',
    'live': '直播',
    'extra': '衍生',
}

# 下拉选择用的说明：给用户看「这一类里都有哪些叫法」，
# 站点换个词也知道该关哪一项。顺序固定，不随字典顺序漂移。
SUBTYPE_HINTS: dict[str, str] = {
    'plus_bonus': '会员Plus版、加长版、悠享版、未删减版、完整版、特别加更、还有加更、加码',
    'pure': '纯享、单人纯享、精编版、高光时刻、沉浸版',
    'bonus': '彩蛋、番外、独家彩蛋',
    'uncut': '花絮、超长花絮、未播片段、幕后、母带放送、花絮集锦',
    'clip': '直拍、单人直拍、第一视角、Fancam、个人cut',
    'prequel': '先导片、先导集、预告、抢先看、前瞻、序章',
    'special': '特辑、特别企划、专场、游戏特辑、复盘、案情回顾',
    'live': '直播、慢直播、陪看、见面会、开播直播夜',
    'extra': '超前营业、超有料、专访、圆桌、盲盒放送',
}


def subtype_label(parsed: 'ParsedTitle') -> str:
    """取衍生类型的中文标签；标题里本来就是中文的写法优先保留。"""
    word = parsed.subtype_word or ''
    if word and not word.isascii():
        return word
    return SUBTYPE_LABELS.get(parsed.subtype or '', word)


# 修饰词：区分「加更」与「还有加更 / 再次加更」这类同季同期同类型的多条记录。
# 这一层是必需的——TMDB 里同一期的「加更」和「还有加更」是两条独立集。
MODIFIER_ALIASES: dict[str, tuple[str, ...]] = {
    'again': ('还有加更', '再次加更', '二次加更', '再加更', '又加更', '多次加更'),
    'advance': ('超前加更', '抢先加更', '提前加更'),
}

_MODIFIER_INDEX: list[tuple[str, str]] = sorted(
    ((alias, canonical) for canonical, aliases in MODIFIER_ALIASES.items() for alias in aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)

# 反向索引：别名 -> 规范类型，按别名长度倒序，长词优先。
_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((alias, canonical) for canonical, aliases in SUBTYPE_ALIASES.items() for alias in aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)

# 纯 ASCII 别名必须按词边界匹配，否则 `plus` 会命中 `surplus`、
# `special` 会命中 `specially` 这类无关单词。
_ASCII_ALIAS_RE: dict[str, re.Pattern[str]] = {
    alias: re.compile(rf'(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])', re.IGNORECASE)
    for alias, _ in _ALIAS_INDEX
    if alias.isascii()
}

# 中文数字
_CN_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}

_CN_NUM = r'[零〇一二两三四五六七八九十百]+'

# 季号
_RE_SEASON_CN = re.compile(rf'第\s*({_CN_NUM}|\d{{1,2}})\s*[季部]')
# 季号允许 1~4 位：国内综艺大量用播出年份当季号（你好星期六 S2026E08、
# 歌手 S2026E05、天赐的声音 S2026E03），只认 1~2 位会把这些剧的季集号
# 整体解析失败，进而认不出这是衍生内容、也回连不上任务。
_RE_SEASON_S = re.compile(r'(?<![A-Za-z0-9])S(\d{1,4})(?![\dEe])')
_RE_YEAR = re.compile(r'(?<!\d)(20\d{2})(?!\d)')

# 期号
_RE_EPISODE_CN = re.compile(rf'第\s*({_CN_NUM}|\d{{1,3}})\s*[期集话回]')
_RE_EPISODE_EP = re.compile(r'(?<![A-Za-z])EP?\s*(\d{1,4})(?!\d)', re.IGNORECASE)
_RE_SEASON_EPISODE = re.compile(r'(?<![A-Za-z0-9])S(\d{1,4})\s*[.\-_ ]?\s*E(\d{1,4})(?!\d)', re.IGNORECASE)

# 日期
_RE_DATE_FULL = re.compile(r'(?<!\d)(20\d{2})[.\-_/年]?(\d{1,2})[.\-_/月]?(\d{1,2})(?!\d)')
_RE_DATE_MD = re.compile(r'(?<![\d.])(\d{1,2})[.\-_/月](\d{1,2})(?![\d.])日?')

# 分P
# 站点两种写法：「第06期上」直接黏在期号后，「第11期（上）」用括号包起来。
# 括号这版很常见（花儿与少年、声生不息、后会友期），漏掉会让同一期的上下
# 两集算成同一个指纹，后到的那条被当重复吞掉，落位也分不清上还是下。
_RE_PART = re.compile(
    r'(?:(?<=[\s.\-_\]】）期集0-9（(])|^)(上|下)(?:集|篇|部)?'
    r'(?=$|[\s.\-_\[\]【（(：:·）)])'
)
# 「第4期上纯享」「第4期下加更」：上/下直接黏在类型词前，中间没有分隔符
_RE_PART_BEFORE_SUBTYPE = re.compile(
    r'(?<=[期集0-9])(上|下)(?=(?:纯享|加更|彩蛋|特辑|花絮|精编|精华|会员|先导|超前|直拍|加料|未播))'
)
_RE_PART_CN = re.compile(r'第?\s*([一二三四五六七八九十])\s*(?:部分|部)')
_RE_PART_EN = re.compile(r'(?:part|pt)\s*([12])(?!\d)', re.IGNORECASE)

# 季集号出现在标题里说明这是「单集」而非整季包
_RE_ANY_SEASON_EPISODE = _RE_SEASON_EPISODE

# 站点副标题里明确标正片，用于把「正片」与「加更」区分开
_RE_FEATURE = re.compile(r'正片')

# 片段名：副标题冒号后的内容（「第04期加更：白敬亭林一…」），
# 或 TMDB 集名里「·」后的内容（「第4季 第4期下纯享·小画家」）。
# 同一期的多个纯享片段只有这一层信息能区分开，是精确落位的最后手段。
_RE_SEGMENT_DOT = re.compile(r'[·・]\s*(.+)$')
# 省略号/尾部标点会污染比对
_RE_TRAILING_DOTS = re.compile(r'[….．\s]+$')
# 站点副标题常在末尾挂别名与标签：「…惊喜合唱 | 声生不息·宝岛季2 / 声生不息2026」。
# 这一截不属于片段名，留在里面会让同一条资源因为别名写法不同算出两个指纹，
# 第二次扫描就被当成新内容重复下载。
_RE_TAIL_ALIAS = re.compile(r'\s*[|｜]\s*')

# 一次性特别内容：没有「第X期」的概念，站点集号只是发布序号
ONE_OFF_SUBTYPES = frozenset({'special', 'bonus', 'prequel', 'live', 'clip'})

# 常见视频技术词，用于剔除干扰
_NOISE_TOKENS = re.compile(
    r'\b(?:2160p|1080p|1080i|720p|480p|4k|8k|uhd|hdr|hdr10|dv|dolby|'
    r'web-?dl|webrip|bluray|blu-?ray|remux|hdtv|x264|x265|h264|h265|hevc|avc|'
    r'aac|ac3|dts|flac|atmos|truehd|ddp?5\.1|'
    r'10bit|8bit|60fps|50fps|25fps|hq|complete|repack|proper)\b',
    re.IGNORECASE,
)


def cn_to_int(text: str) -> Optional[int]:
    """中文数字转整数，支持 1-999 的常见写法。"""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if all(ch in _CN_DIGITS for ch in text):
        # 纯位值写法，如「一七」
        value = 0
        for ch in text:
            value = value * 10 + _CN_DIGITS[ch]
        return value
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch == '十':
            section += (number or 1) * 10
            number = 0
        elif ch == '百':
            section += (number or 1) * 100
            number = 0
        else:
            return None
    return total + section + number


@dataclass(slots=True)
class ParsedTitle:
    """标题解析结果。字段为 None 表示标题中未出现该信号。"""

    raw: str
    season: Optional[int] = None
    episode: Optional[int] = None
    site_season: Optional[int] = None    # 站点 SxxExx 里的季号
    site_episode: Optional[int] = None   # 站点 SxxExx 里的集号，可能只是发布序号
    subtype: Optional[str] = None
    subtype_word: Optional[str] = None
    modifier: Optional[str] = None   # again / advance，用于区分同期的多次加更
    part: Optional[str] = None
    year: Optional[int] = None
    air_date: Optional[date] = None
    is_pack: bool = False          # 整季包（无单集集号）
    is_single_episode: bool = False
    # 期号来源：site（站点 SxxExx）/ title（标题里的第X期）/ desc（副标题里的第X期）。
    # 站点编号可能只是发布序号，不能当期号用，匹配时要区别对待。
    episode_source: str = ''
    # 片段名，如「小画家」「白敬亭林一雷霆画功」，用于区分同期的多个纯享片段
    segment: Optional[str] = None
    signals: list[str] = field(default_factory=list)
    # 副标题明确标了「正片」：这是正片本身，不是衍生内容，插件不参与
    is_feature: bool = False

    @property
    def episode_is_site_ordinal(self) -> bool:
        """期号是否只是站点发布序号（不是节目真实期号）。"""
        return self.episode_source == 'site'

    @property
    def is_derived(self) -> bool:
        """是否衍生内容（加更/彩蛋/纯享等）。"""
        return self.subtype is not None


def _clean(text: str) -> str:
    """去掉技术规格与括号内的发布组信息，降低误判。"""
    cleaned = _NOISE_TOKENS.sub(' ', text)
    # 去掉方括号内容（极影视本身也会忽略 [] 内内容，这里提前剔除避免误读集号）
    cleaned = re.sub(r'\[[^\]]*\]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def detect_subtype(text: str) -> tuple[Optional[str], Optional[str]]:
    """识别衍生类型，返回 (规范类型, 命中的原始别名)。

    纯 ASCII 别名按词边界匹配（`Plus` 不会命中 `surplus`），
    中文别名按子串匹配（中文没有词边界概念）。
    """
    lowered = text.lower()
    for alias, canonical in _ALIAS_INDEX:
        if alias.isascii():
            if _ASCII_ALIAS_RE[alias].search(text):
                return canonical, alias
        elif alias.lower() in lowered:
            return canonical, alias
    return None, None


def detect_modifier(text: str) -> Optional[str]:
    """识别「还有加更 / 再次加更 / 超前加更」这类修饰语。"""
    for alias, canonical in _MODIFIER_INDEX:
        if alias in text:
            return canonical
    return None


def _extract_part(text: str) -> Optional[str]:
    if m := _RE_PART_EN.search(text):
        return '上' if m.group(1) == '1' else '下'
    if m := _RE_PART_CN.search(text):
        value = cn_to_int(m.group(1))
        return {1: '上', 2: '中', 3: '下'}.get(value or 0)
    if m := _RE_PART_BEFORE_SUBTYPE.search(text):
        return m.group(1)
    # 「上/下」必须贴近类型词或位于结尾，避免「上海」这类误判
    for m in _RE_PART.finditer(text):
        # 括号写法「第11期（上）：…」：右括号要先剥掉再看后面接什么
        tail = text[m.end():].lstrip('）)').strip(' .-_')
        if not tail:
            return m.group(1)
        # 形如「第4期下：小画家」：分P后面直接接片段名
        if tail[:1] in '：:·|':
            return m.group(1)
        # 形如「第1期.上.加更」：后面紧跟类型词时也算分P
        if len(tail) <= 8 and detect_subtype(tail)[0]:
            return m.group(1)
        # 形如「第4期上纯享·不眨眼唱歌」：类型词紧跟其后，片段名再长也不影响
        if detect_subtype(tail[:6])[0]:
            return m.group(1)
    return None


def _cut_alias(value: str) -> str:
    """切掉片段名尾部的「| 别名 / 标签」，再清掉省略号和包裹符号。"""
    value = _RE_TAIL_ALIAS.split(value, maxsplit=1)[0]
    return _RE_TRAILING_DOTS.sub('', value).strip(' -_|/·')


def _extract_segment(text: str) -> Optional[str]:
    """提取片段名。

    副标题形如「现在就出发 第四季 加更 第04期还有加更：白敬亭林一雷霆画功…」，
    冒号后是这一条衍生内容的片段名；TMDB 集名形如「第4季 第4期下纯享·小画家」，
    「·」后是同一个片段名。两者对上，说明就是同一段视频。
    """
    # 冒号可能不止一个：「现在就出发：第四季 第04期纯享：小画家」去掉剧名后
    # 还留着前导冒号，第一个冒号前缀既没有期号也没有类型词，那不是片段名的
    # 分隔符。要依次往后找，而不是碰上第一个冒号判失败就整个放弃——
    # 放弃的后果是不同片段算出同一个指纹，第二份被当成重复内容吞掉。
    for m in re.finditer(r'[：:]', text):
        prefix = text[: m.start()]
        if not (_RE_EPISODE_CN.search(prefix) or detect_subtype(prefix)[0]):
            continue
        value = _cut_alias(text[m.end():])
        if len(value) >= 2:
            return value
    if m := _RE_SEGMENT_DOT.search(text):
        value = _cut_alias(m.group(1))
        if len(value) >= 2:
            return value
    return None


def _extract_date(text: str) -> tuple[Optional[date], Optional[int]]:
    """提取播出日期，返回 (date, 年份)。"""
    if m := _RE_DATE_FULL.search(text):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day), year
        except ValueError:
            return None, year
    if m := _RE_DATE_MD.search(text):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return None, None  # 缺年份，无法定位，交给季推断
    return None, None


def parse_title(title: str) -> ParsedTitle:
    """解析一条发布标题。

    优先级：SxxExx > 第X季 + 第X期 > 仅第X期。
    季号缺失时保留 None，由匹配器用年份或剧集季表补全。
    """
    raw = title or ''
    text = _clean(raw)
    parsed = ParsedTitle(raw=raw)

    # 季集号（先记录站点原始编号，后面文本期号会覆盖 episode）
    if m := _RE_ANY_SEASON_EPISODE.search(text):
        parsed.site_season = int(m.group(1))
        parsed.site_episode = int(m.group(2))
        parsed.season = parsed.site_season
        parsed.episode = parsed.site_episode
        parsed.episode_source = 'site'
        parsed.is_single_episode = True
        parsed.signals.append('SxxExx')

    # 第X季
    if parsed.season is None:
        if m := _RE_SEASON_CN.search(text):
            if (value := cn_to_int(m.group(1))) is not None:
                parsed.season = value
                parsed.signals.append('第X季')
        elif m := _RE_SEASON_S.search(text):
            parsed.season = int(m.group(1))
            parsed.signals.append('Sxx')

    # 期号：标题里写明的「第X期」才是真实期号，优先级高于站点 SxxExx
    if m := _RE_EPISODE_CN.search(text):
        if (value := cn_to_int(m.group(1))) is not None:
            parsed.episode = value
            parsed.episode_source = 'title'
            parsed.signals.append('第X期')
    elif parsed.episode is None:
        if m := _RE_EPISODE_EP.search(text):
            parsed.episode = int(m.group(1))
            parsed.episode_source = 'title'
            parsed.signals.append('EPx')

    # 年份与日期
    air_date, year = _extract_date(text)
    parsed.air_date = air_date
    if year:
        parsed.year = year
        parsed.signals.append('year')
    elif m := _RE_YEAR.search(text):
        parsed.year = int(m.group(1))
        parsed.signals.append('year')

    # 衍生类型
    subtype, word = detect_subtype(text)
    parsed.subtype = subtype
    parsed.subtype_word = word
    parsed.modifier = detect_modifier(text)

    # 分P
    parsed.part = _extract_part(text)

    # 片段名
    parsed.segment = _extract_segment(text)

    # 整季包判定：有季号无期号、且标题无集号
    parsed.is_pack = parsed.season is not None and parsed.episode is None and not parsed.is_single_episode

    return parsed


def strip_show_name(title: str, show_names: list[str]) -> str:
    """从标题里剥离剧名，避免剧名里带「第X期」这类字样干扰解析。"""
    text = title or ''
    for name in sorted(show_names, key=len, reverse=True):
        if not name:
            continue
        if name in text:
            text = text.replace(name, ' ')
    return text


def parse_release(title: str, description: str = '') -> ParsedTitle:
    """联合解析资源名与站点副标题。

    PT 站点的资源名通常是「英文剧名 + SxxExx + 压制参数」，例如::

        Natural High S04E08 Plus 2026 2160p TX WEB-DL H265 DDP2.0-ADWeb

    而节目真实的中文期号与类型写在副标题里::

        现在就出发 第四季 加更 第04期还有加更：白敬亭林一雷霆画功…

    所以分工是：**资源名提供站点编号与技术规格，副标题提供期号、类型、季号**。
    副标题里的「第X期」是节目真实期号，优先级高于站点 SxxExx
    ——站点 E08 往往只是发布序号，和真实期号不是一回事。
    """
    parsed = parse_title(title or '')
    description = (description or '').strip()
    if not description:
        # 没有副标题也不能把 Special / Eggs / Prologue 这类一次性内容的
        # 站点集号当成真实期号：同一份资源带副标题时能落位、不带副标题时
        # 拿着发布序号去抢某一集，才是真正会刮错的那种不稳定。
        # 只清「站点序号」来的期号——标题里明写的「第X期」是真期号，留着。
        if parsed.subtype in ONE_OFF_SUBTYPES and parsed.episode_source == 'site':
            parsed.episode = None
            parsed.episode_source = ''
            parsed.signals.append('一次性特别:不采用站点集号')
        return parsed

    extra = parse_title(description)

    if extra.episode is not None:
        parsed.episode = extra.episode
        parsed.episode_source = 'desc'
        parsed.signals.append('副标题:期号')
    if extra.season is not None:
        parsed.season = extra.season
        parsed.signals.append('副标题:季号')
    if extra.subtype:
        parsed.subtype = extra.subtype
        parsed.subtype_word = extra.subtype_word
        parsed.signals.append(f'副标题:类型={extra.subtype_word}')
    if extra.modifier:
        parsed.modifier = extra.modifier
    if extra.part and not parsed.part:
        parsed.part = extra.part
    # 副标题的片段名比资源名可靠：资源名里没有片段信息
    if extra.segment:
        parsed.segment = extra.segment
        parsed.signals.append('副标题:片段名')
    if parsed.air_date is None and extra.air_date:
        parsed.air_date = extra.air_date
    if parsed.year is None and extra.year:
        parsed.year = extra.year

    # 副标题明确标「正片」时，不把资源名里的 Plus 之类当成衍生标记：
    # 这类资源是正片本身，应由用户的正片订阅处理，插件不介入。
    if _RE_FEATURE.search(description) and not extra.subtype:
        parsed.subtype = None
        parsed.subtype_word = None
        parsed.modifier = None
        # 光清衍生标记不够：正片同样带季号、期号、分P、片段名，
        # 拿去做落位计算会稳稳命中同期的衍生集（第6期上正片撞第6期上纯享），
        # 于是正片被插件下走、塞进 Season 00。这里必须留下明确的「是正片」标记，
        # 由扫描链路整条排除。
        parsed.is_feature = True
        parsed.signals.append('副标题:正片')

    # 「毛雪汪 S01E02 Special」这类一次性特别内容没有期号概念，
    # 站点 E02 只是发布序号，拿去和 TMDB 的「第X期」比必然错位。
    # 同样只清站点序号：标题或副标题里明写的「第X期」是真期号。
    if (
        extra.episode is None
        and parsed.subtype in ONE_OFF_SUBTYPES
        and parsed.episode_source == 'site'
    ):
        parsed.episode = None
        parsed.episode_source = ''
        parsed.signals.append('一次性特别:不采用站点集号')

    parsed.raw = f'{title} || {description}'
    return parsed
