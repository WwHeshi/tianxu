"""Versioned, deterministic BaZi shen-sha rules.

Shen-sha catalogs differ between schools.  This module deliberately keeps a
documented, finite v2 catalog of 51 names instead of treating almanac day gods
as BaZi shen-sha.  Rules operate only on the four finalized pillars, so they
also obey the engine's selected year, month, day and true-solar-time policies.
"""

from __future__ import annotations

from collections.abc import Mapping

from lunar_python.util import LunarUtil

SHEN_SHA_POLICY_VERSION = "v2"
PILLAR_NAMES = ("year", "month", "day", "hour")
GENDER_VALUES = ("male", "female")

# This order is part of the API presentation contract.
SHEN_SHA_DISPLAY_ORDER = (
    "天乙贵人",
    "太极贵人",
    "天德贵人",
    "月德贵人",
    "天德合",
    "月德合",
    "福星贵人",
    "天医",
    "文昌贵人",
    "学堂",
    "词馆",
    "国印贵人",
    "德秀贵人",
    "金舆",
    "金神",
    "五鬼",
    "天赦",
    "流霞",
    "红艳",
    "禄神",
    "羊刃",
    "飞刃",
    "血刃",
    "驿马",
    "华盖",
    "桃花",
    "将星",
    "劫煞",
    "亡神",
    "天罗",
    "地网",
    "十灵日",
    "魁罡",
    "八专",
    "九丑",
    "阴阳差错",
    "十恶大败",
    "四废",
    "孤鸾",
    "红鸾",
    "天喜",
    "披麻",
    "孤辰",
    "寡宿",
    "元辰",
    "童子",
    "天厨",
    "吊客",
    "丧门",
    "灾煞",
    "空亡",
)

DAY_STEM_BRANCH_RULES: tuple[tuple[str, Mapping[str, str]], ...] = (
    (
        "天乙贵人",
        {
            "甲": "丑未",
            "乙": "子申",
            "丙": "亥酉",
            "丁": "亥酉",
            "戊": "丑未",
            "己": "子申",
            "庚": "丑未",
            "辛": "寅午",
            "壬": "卯巳",
            "癸": "卯巳",
        },
    ),
    (
        "太极贵人",
        {
            "甲": "子午",
            "乙": "子午",
            "丙": "卯酉",
            "丁": "卯酉",
            "戊": "辰戌丑未",
            "己": "辰戌丑未",
            "庚": "寅亥",
            "辛": "寅亥",
            "壬": "巳申",
            "癸": "巳申",
        },
    ),
    (
        "文昌贵人",
        {
            "甲": "巳",
            "乙": "午",
            "丙": "申",
            "丁": "酉",
            "戊": "申",
            "己": "酉",
            "庚": "亥",
            "辛": "子",
            "壬": "寅",
            "癸": "卯",
        },
    ),
    (
        "国印贵人",
        {
            "甲": "戌",
            "乙": "亥",
            "丙": "丑",
            "丁": "寅",
            "戊": "丑",
            "己": "寅",
            "庚": "辰",
            "辛": "巳",
            "壬": "未",
            "癸": "申",
        },
    ),
    (
        "金舆",
        {
            "甲": "辰",
            "乙": "巳",
            "丙": "未",
            "丁": "申",
            "戊": "未",
            "己": "申",
            "庚": "戌",
            "辛": "亥",
            "壬": "丑",
            "癸": "寅",
        },
    ),
    (
        "流霞",
        {
            "甲": "酉",
            "乙": "戌",
            "丙": "未",
            "丁": "申",
            "戊": "巳",
            "己": "午",
            "庚": "辰",
            "辛": "卯",
            "壬": "亥",
            "癸": "寅",
        },
    ),
    (
        "禄神",
        {
            "甲": "寅",
            "乙": "卯",
            "丙": "巳",
            "丁": "午",
            "戊": "巳",
            "己": "午",
            "庚": "申",
            "辛": "酉",
            "壬": "亥",
            "癸": "子",
        },
    ),
    (
        "羊刃",
        {
            "甲": "卯",
            "乙": "寅",
            "丙": "午",
            "丁": "巳",
            "戊": "午",
            "己": "巳",
            "庚": "酉",
            "辛": "申",
            "壬": "子",
            "癸": "亥",
        },
    ),
)

FORTUNE_STAR_BRANCHES = {
    "甲": "寅子",
    "乙": "卯丑",
    "丙": "寅子",
    "丁": "亥",
    "戊": "申",
    "己": "未",
    "庚": "午",
    "辛": "巳",
    "壬": "辰",
    "癸": "卯丑",
}
RED_BEAUTY_BRANCHES = {
    "甲": "午",
    "乙": "午",
    "丙": "寅",
    "丁": "未",
    "戊": "辰",
    "己": "辰",
    "庚": "戌",
    "辛": "酉",
    "壬": "子",
    "癸": "申",
}
FLYING_BLADE_BRANCHES = {
    "甲": "酉",
    "乙": "申",
    "丙": "子",
    "丁": "丑",
    "戊": "子",
    "己": "丑",
    "庚": "卯",
    "辛": "辰",
    "壬": "午",
    "癸": "未",
}

SCHOOL_BRANCH_BY_NA_YIN = {"金": "巳", "木": "亥", "水": "申", "土": "申", "火": "寅"}
CI_GUAN_BRANCH_BY_NA_YIN = {
    "金": "申",
    "木": "寅",
    "水": "亥",
    "土": "亥",
    "火": "巳",
}
GOLDEN_GOD_PILLARS = frozenset(("乙丑", "己巳", "癸酉"))
FIVE_GHOSTS_BRANCH = dict(
    zip("子丑寅卯辰巳午未申酉戌亥", "辰巳午未申酉戌亥子丑寅卯", strict=True)
)
HEAVENLY_PARDON_DAY = {
    "寅卯辰": "戊寅",
    "巳午未": "甲午",
    "申酉戌": "戊申",
    "亥子丑": "甲子",
}
HEAVENLY_NET_BRANCH = {"戌": "亥", "亥": "戌"}
EARTHLY_NET_BRANCH = {"辰": "巳", "巳": "辰"}
BLOOD_BLADE_BRANCH = {
    "寅": "丑",
    "卯": "未",
    "辰": "寅",
    "巳": "申",
    "午": "卯",
    "未": "酉",
    "申": "辰",
    "酉": "戌",
    "戌": "巳",
    "亥": "亥",
    "子": "午",
    "丑": "子",
}
EIGHT_EXCLUSIVE_DAYS = frozenset("甲寅 乙卯 丁未 戊戌 己未 庚申 辛酉 癸丑".split())
NINE_UGLY_DAYS = frozenset("丁酉 戊子 戊午 己卯 己酉 辛卯 辛酉 壬子 壬午".split())
YUAN_CHEN_YANG_MALE_YIN_FEMALE = dict(
    zip("子丑寅卯辰巳午未申酉戌亥", "未申酉戌亥子丑寅卯辰巳午", strict=True)
)
YUAN_CHEN_YIN_MALE_YANG_FEMALE = dict(
    zip("子丑寅卯辰巳午未申酉戌亥", "巳午未申酉戌亥子丑寅卯辰", strict=True)
)
YANG_STEMS = frozenset("甲丙戊庚壬")
CHILD_GOD_SEASON_BRANCHES = {
    "寅卯辰": "寅子",
    "申酉戌": "寅子",
    "巳午未": "卯未辰",
    "亥子丑": "卯未辰",
}
CHILD_GOD_NA_YIN_BRANCHES = {
    "金": "午卯",
    "木": "午卯",
    "水": "酉戌",
    "火": "酉戌",
    "土": "辰巳",
}
HEAVENLY_KITCHEN_BRANCHES = {
    "丙": "巳",
    "丁": "午",
    "戊": "申",
    "己": "酉",
    "庚": "亥",
    "辛": "子",
    "壬": "寅",
    "癸": "卯",
}
TEN_DEFEAT_DAYS = frozenset(
    "甲辰 乙巳 丙申 丁亥 戊戌 己丑 庚辰 辛巳 壬申 癸亥".split()
)
SOLITARY_PHOENIX_DAYS = frozenset("甲寅 乙巳 丙午 丁巳 戊午 戊申 辛亥 壬子".split())

TRINE_GROUPS = ("申子辰", "寅午戌", "巳酉丑", "亥卯未")
DAY_BRANCH_STARS = {
    "驿马": {"申子辰": "寅", "寅午戌": "申", "巳酉丑": "亥", "亥卯未": "巳"},
    "华盖": {"申子辰": "辰", "寅午戌": "戌", "巳酉丑": "丑", "亥卯未": "未"},
    "桃花": {"申子辰": "酉", "寅午戌": "卯", "巳酉丑": "午", "亥卯未": "子"},
    "将星": {"申子辰": "子", "寅午戌": "午", "巳酉丑": "酉", "亥卯未": "卯"},
    "劫煞": {"申子辰": "巳", "寅午戌": "亥", "巳酉丑": "寅", "亥卯未": "申"},
    "亡神": {"申子辰": "亥", "寅午戌": "巳", "巳酉丑": "申", "亥卯未": "寅"},
}
DISASTER_STAR = {"申子辰": "午", "寅午戌": "子", "巳酉丑": "卯", "亥卯未": "酉"}

TIAN_DE = {
    "寅": "丁",
    "卯": "申",
    "辰": "壬",
    "巳": "辛",
    "午": "亥",
    "未": "甲",
    "申": "癸",
    "酉": "寅",
    "戌": "丙",
    "亥": "乙",
    "子": "巳",
    "丑": "庚",
}
TIAN_DE_HE = {
    "寅": "壬",
    "卯": "巳",
    "辰": "丁",
    "巳": "丙",
    "午": "寅",
    "未": "己",
    "申": "戊",
    "酉": "亥",
    "戌": "辛",
    "亥": "庚",
    "子": "申",
    "丑": "乙",
}
YUE_DE = {"申子辰": "壬", "寅午戌": "丙", "巳酉丑": "庚", "亥卯未": "甲"}
YUE_DE_HE = {"申子辰": "丁", "寅午戌": "辛", "巳酉丑": "乙", "亥卯未": "己"}
DE_XIU = {
    "寅午戌": (frozenset("丙丁"), frozenset("戊癸")),
    "申子辰": (frozenset("壬癸戊己"), frozenset("丙辛甲己")),
    "巳酉丑": (frozenset("庚辛"), frozenset("乙庚")),
    "亥卯未": (frozenset("甲乙"), frozenset("丁壬")),
}

RED_LUAN = dict(zip("子丑寅卯辰巳午未申酉戌亥", "卯寅丑子亥戌酉申未午巳辰", strict=True))
TIAN_XI = dict(zip("子丑寅卯辰巳午未申酉戌亥", "酉申未午巳辰卯寅丑子亥戌", strict=True))
SOLITARY_STARS = {
    "亥子丑": ("寅", "戌"),
    "寅卯辰": ("巳", "丑"),
    "巳午未": ("申", "辰"),
    "申酉戌": ("亥", "未"),
}

TEN_SPIRIT_DAYS = frozenset("甲辰 乙亥 丙辰 丁酉 戊午 庚戌 庚寅 辛亥 壬寅 癸未".split())
KUI_GANG_DAYS = frozenset("戊戌 庚辰 庚戌 壬辰".split())
YIN_YANG_ERROR_DAYS = frozenset(
    "丙子 丁丑 戊寅 辛卯 壬辰 癸巳 丙午 丁未 戊申 辛酉 壬戌 癸亥".split()
)
FOUR_WASTE_DAYS = {
    "寅卯辰": frozenset(("庚申", "辛酉")),
    "巳午未": frozenset(("壬子", "癸亥")),
    "申酉戌": frozenset(("甲寅", "乙卯")),
    "亥子丑": frozenset(("丙午", "丁巳")),
}


def _group_for(branch: str, groups: tuple[str, ...] = TRINE_GROUPS) -> str:
    for group in groups:
        if branch in group:
            return group
    raise ValueError(f"unknown earthly branch: {branch!r}")


def _add_by_branch(
    matches: dict[str, set[str]], pillars: Mapping[str, str], name: str, targets: str
) -> None:
    for pillar_name, gan_zhi in pillars.items():
        if gan_zhi[1] in targets:
            matches[pillar_name].add(name)


def _add_by_stem(
    matches: dict[str, set[str]], pillars: Mapping[str, str], name: str, targets: str
) -> None:
    for pillar_name, gan_zhi in pillars.items():
        if gan_zhi[0] in targets:
            matches[pillar_name].add(name)


def _add_by_symbol(
    matches: dict[str, set[str]], pillars: Mapping[str, str], name: str, target: str
) -> None:
    if target in LunarUtil.GAN:
        _add_by_stem(matches, pillars, name, target)
    else:
        _add_by_branch(matches, pillars, name, target)


def _na_yin_element(gan_zhi: str) -> str:
    try:
        return LunarUtil.NAYIN[gan_zhi][-1]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"unknown sexagenary-cycle pillar: {gan_zhi!r}") from exc


def calculate_shen_sha(
    pillars: Mapping[str, str], *, gender: str
) -> dict[str, list[str]]:
    """Return the v2 shen-sha matches for each finalized pillar."""

    if set(pillars) != set(PILLAR_NAMES) or any(len(value) != 2 for value in pillars.values()):
        raise ValueError("shen-sha calculation requires year, month, day and hour pillars")
    if gender not in GENDER_VALUES:
        raise ValueError(f"unsupported gender for shen-sha calculation: {gender!r}")

    matches = {name: set() for name in PILLAR_NAMES}
    year_gan_zhi = pillars["year"]
    month_gan_zhi = pillars["month"]
    day_gan_zhi = pillars["day"]
    year_stem, year_branch = year_gan_zhi
    month_branch = month_gan_zhi[1]
    day_stem, day_branch = day_gan_zhi
    year_na_yin_element = _na_yin_element(year_gan_zhi)

    for name, table in DAY_STEM_BRANCH_RULES:
        _add_by_branch(matches, pillars, name, table[day_stem])

    fortune_star_targets = FORTUNE_STAR_BRANCHES[year_stem] + FORTUNE_STAR_BRANCHES[day_stem]
    _add_by_branch(matches, pillars, "福星贵人", fortune_star_targets)
    _add_by_branch(
        matches,
        pillars,
        "学堂",
        SCHOOL_BRANCH_BY_NA_YIN[year_na_yin_element],
    )
    _add_by_branch(
        matches,
        pillars,
        "词馆",
        CI_GUAN_BRANCH_BY_NA_YIN[year_na_yin_element],
    )

    for pillar_name in ("day", "hour"):
        if pillars[pillar_name] in GOLDEN_GOD_PILLARS:
            matches[pillar_name].add("金神")

    _add_by_branch(matches, pillars, "五鬼", FIVE_GHOSTS_BRANCH[month_branch])
    pardon_season = _group_for(month_branch, tuple(HEAVENLY_PARDON_DAY))
    if day_gan_zhi == HEAVENLY_PARDON_DAY[pardon_season]:
        matches["day"].add("天赦")

    _add_by_branch(matches, pillars, "红艳", RED_BEAUTY_BRANCHES[day_stem])
    _add_by_branch(matches, pillars, "飞刃", FLYING_BLADE_BRANCHES[day_stem])
    _add_by_branch(matches, pillars, "血刃", BLOOD_BLADE_BRANCH[month_branch])

    heavenly_net_targets = HEAVENLY_NET_BRANCH.get(year_branch, "") + HEAVENLY_NET_BRANCH.get(
        day_branch, ""
    )
    earthly_net_targets = EARTHLY_NET_BRANCH.get(year_branch, "") + EARTHLY_NET_BRANCH.get(
        day_branch, ""
    )
    _add_by_branch(matches, pillars, "天罗", heavenly_net_targets)
    _add_by_branch(matches, pillars, "地网", earthly_net_targets)

    if day_gan_zhi in EIGHT_EXCLUSIVE_DAYS:
        matches["day"].add("八专")
    if day_gan_zhi in NINE_UGLY_DAYS:
        matches["day"].add("九丑")

    uses_yang_male_yin_female = (gender == "male") == (year_stem in YANG_STEMS)
    yuan_chen_table = (
        YUAN_CHEN_YANG_MALE_YIN_FEMALE
        if uses_yang_male_yin_female
        else YUAN_CHEN_YIN_MALE_YANG_FEMALE
    )
    _add_by_branch(matches, pillars, "元辰", yuan_chen_table[year_branch])

    child_god_season = _group_for(month_branch, tuple(CHILD_GOD_SEASON_BRANCHES))
    child_god_targets = (
        CHILD_GOD_SEASON_BRANCHES[child_god_season]
        + CHILD_GOD_NA_YIN_BRANCHES[year_na_yin_element]
    )
    for pillar_name in ("day", "hour"):
        if pillars[pillar_name][1] in child_god_targets:
            matches[pillar_name].add("童子")

    heavenly_kitchen_targets = HEAVENLY_KITCHEN_BRANCHES.get(
        year_stem, ""
    ) + HEAVENLY_KITCHEN_BRANCHES.get(day_stem, "")
    _add_by_branch(matches, pillars, "天厨", heavenly_kitchen_targets)

    if day_gan_zhi in TEN_DEFEAT_DAYS:
        matches["day"].add("十恶大败")
    if day_gan_zhi in SOLITARY_PHOENIX_DAYS:
        matches["day"].add("孤鸾")

    month_group = _group_for(month_branch)
    _add_by_symbol(matches, pillars, "天德贵人", TIAN_DE[month_branch])
    _add_by_symbol(matches, pillars, "天德合", TIAN_DE_HE[month_branch])
    _add_by_stem(matches, pillars, "月德贵人", YUE_DE[month_group])
    _add_by_stem(matches, pillars, "月德合", YUE_DE_HE[month_group])
    _add_by_branch(
        matches,
        pillars,
        "天医",
        LunarUtil.ZHI[(LunarUtil.ZHI.index(month_branch) - 1) % 12 or 12],
    )

    de_stems, xiu_stems = DE_XIU[month_group]
    chart_stems = {gan_zhi[0] for gan_zhi in pillars.values()}
    if chart_stems & de_stems and chart_stems & xiu_stems:
        for pillar_name, gan_zhi in pillars.items():
            if gan_zhi[0] in de_stems | xiu_stems:
                matches[pillar_name].add("德秀贵人")

    day_group = _group_for(day_branch)
    for name, table in DAY_BRANCH_STARS.items():
        _add_by_branch(matches, pillars, name, table[day_group])

    _add_by_branch(matches, pillars, "红鸾", RED_LUAN[year_branch])
    _add_by_branch(matches, pillars, "天喜", TIAN_XI[year_branch])
    year_index = LunarUtil.ZHI.index(year_branch) - 1
    _add_by_branch(matches, pillars, "披麻", LunarUtil.ZHI[(year_index - 3) % 12 + 1])
    _add_by_branch(matches, pillars, "吊客", LunarUtil.ZHI[(year_index - 2) % 12 + 1])
    _add_by_branch(matches, pillars, "丧门", LunarUtil.ZHI[(year_index + 2) % 12 + 1])

    solitary_group = _group_for(year_branch, tuple(SOLITARY_STARS))
    solitary_target, widow_target = SOLITARY_STARS[solitary_group]
    _add_by_branch(matches, pillars, "孤辰", solitary_target)
    _add_by_branch(matches, pillars, "寡宿", widow_target)
    _add_by_branch(matches, pillars, "灾煞", DISASTER_STAR[_group_for(year_branch)])

    void_branches = set(LunarUtil.getXunKong(year_gan_zhi) + LunarUtil.getXunKong(day_gan_zhi))
    _add_by_branch(matches, pillars, "空亡", "".join(void_branches))

    if day_gan_zhi in TEN_SPIRIT_DAYS:
        matches["day"].add("十灵日")
    if day_gan_zhi in KUI_GANG_DAYS:
        matches["day"].add("魁罡")
    if day_gan_zhi in YIN_YANG_ERROR_DAYS:
        matches["day"].add("阴阳差错")
    season_group = _group_for(month_branch, tuple(FOUR_WASTE_DAYS))
    if day_gan_zhi in FOUR_WASTE_DAYS[season_group]:
        matches["day"].add("四废")

    order = {name: index for index, name in enumerate(SHEN_SHA_DISPLAY_ORDER)}
    return {
        pillar_name: sorted(pillar_matches, key=order.__getitem__)
        for pillar_name, pillar_matches in matches.items()
    }
