import pytest

from app.bazi.shen_sha import SHEN_SHA_DISPLAY_ORDER, calculate_shen_sha

EXTENDED_SHEN_SHA_NAMES = (
    "福星贵人",
    "学堂",
    "词馆",
    "金神",
    "五鬼",
    "天赦",
    "红艳",
    "天罗",
    "地网",
    "飞刃",
    "血刃",
    "八专",
    "九丑",
    "元辰",
    "童子",
    "天厨",
    "十恶大败",
    "孤鸾",
)


def test_v2_catalog_contains_51_unique_names() -> None:
    assert len(SHEN_SHA_DISPLAY_ORDER) == 51
    assert len(set(SHEN_SHA_DISPLAY_ORDER)) == 51
    assert set(EXTENDED_SHEN_SHA_NAMES) <= set(SHEN_SHA_DISPLAY_ORDER)


@pytest.mark.parametrize(
    (
        "name",
        "positive_pillars",
        "positive_gender",
        "expected_pillar",
        "negative_pillars",
        "negative_gender",
    ),
    (
        pytest.param(
            "福星贵人",
            {"year": "甲子", "month": "丙寅", "day": "庚辰", "hour": "辛巳"},
            "male",
            "month",
            {"year": "乙亥", "month": "丙寅", "day": "庚辰", "hour": "辛巳"},
            "male",
            id="fortune-star",
        ),
        pytest.param(
            "学堂",
            {"year": "甲子", "month": "丙寅", "day": "庚午", "hour": "己巳"},
            "male",
            "hour",
            {"year": "甲子", "month": "丙寅", "day": "庚午", "hour": "戊辰"},
            "male",
            id="school",
        ),
        pytest.param(
            "词馆",
            {"year": "甲子", "month": "丙寅", "day": "庚午", "hour": "壬申"},
            "male",
            "hour",
            {"year": "甲子", "month": "丙寅", "day": "庚午", "hour": "戊辰"},
            "male",
            id="ci-guan",
        ),
        pytest.param(
            "金神",
            {"year": "甲子", "month": "丙寅", "day": "乙丑", "hour": "庚辰"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "乙卯", "hour": "庚辰"},
            "male",
            id="golden-god",
        ),
        pytest.param(
            "五鬼",
            {"year": "甲戌", "month": "丙子", "day": "庚辰", "hour": "辛未"},
            "male",
            "day",
            {"year": "甲戌", "month": "丙子", "day": "甲寅", "hour": "辛未"},
            "male",
            id="five-ghosts",
        ),
        pytest.param(
            "天赦",
            {"year": "甲子", "month": "丙寅", "day": "戊寅", "hour": "辛酉"},
            "male",
            "day",
            {"year": "甲子", "month": "己巳", "day": "戊寅", "hour": "辛酉"},
            "male",
            id="heavenly-pardon",
        ),
        pytest.param(
            "红艳",
            {"year": "甲子", "month": "丙寅", "day": "甲辰", "hour": "庚午"},
            "male",
            "hour",
            {"year": "甲子", "month": "丙寅", "day": "甲辰", "hour": "己巳"},
            "male",
            id="red-beauty",
        ),
        pytest.param(
            "天罗",
            {"year": "甲戌", "month": "乙亥", "day": "庚申", "hour": "壬午"},
            "male",
            "month",
            {"year": "甲戌", "month": "丙子", "day": "庚申", "hour": "壬午"},
            "male",
            id="heavenly-net",
        ),
        pytest.param(
            "地网",
            {"year": "甲辰", "month": "己巳", "day": "庚申", "hour": "壬午"},
            "male",
            "month",
            {"year": "甲辰", "month": "戊午", "day": "庚申", "hour": "壬子"},
            "male",
            id="earthly-net",
        ),
        pytest.param(
            "飞刃",
            {"year": "甲子", "month": "丙寅", "day": "甲辰", "hour": "癸酉"},
            "male",
            "hour",
            {"year": "甲子", "month": "丙寅", "day": "甲辰", "hour": "壬申"},
            "male",
            id="flying-blade",
        ),
        pytest.param(
            "血刃",
            {"year": "甲戌", "month": "丙寅", "day": "乙丑", "hour": "壬午"},
            "male",
            "day",
            {"year": "甲戌", "month": "丙寅", "day": "甲子", "hour": "己巳"},
            "male",
            id="blood-blade",
        ),
        pytest.param(
            "八专",
            {"year": "甲子", "month": "丙寅", "day": "甲寅", "hour": "辛酉"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "甲子", "hour": "辛酉"},
            "male",
            id="eight-exclusive",
        ),
        pytest.param(
            "九丑",
            {"year": "甲子", "month": "丙寅", "day": "丁酉", "hour": "庚辰"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "丁亥", "hour": "庚辰"},
            "male",
            id="nine-ugly",
        ),
        pytest.param(
            "元辰",
            {"year": "甲子", "month": "辛未", "day": "丙寅", "hour": "庚子"},
            "male",
            "month",
            {"year": "甲子", "month": "辛未", "day": "丙寅", "hour": "庚子"},
            "female",
            id="yuan-chen",
        ),
        pytest.param(
            "童子",
            {"year": "甲子", "month": "丙寅", "day": "甲子", "hour": "辛酉"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "甲戌", "hour": "辛酉"},
            "male",
            id="child-god",
        ),
        pytest.param(
            "天厨",
            {"year": "丙寅", "month": "戊子", "day": "甲戌", "hour": "己巳"},
            "male",
            "hour",
            {"year": "丙寅", "month": "戊子", "day": "甲戌", "hour": "庚午"},
            "male",
            id="heavenly-kitchen",
        ),
        pytest.param(
            "十恶大败",
            {"year": "甲子", "month": "丙寅", "day": "甲辰", "hour": "辛酉"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "甲寅", "hour": "辛酉"},
            "male",
            id="ten-defeats",
        ),
        pytest.param(
            "孤鸾",
            {"year": "甲子", "month": "丙寅", "day": "甲寅", "hour": "辛酉"},
            "male",
            "day",
            {"year": "甲子", "month": "丙寅", "day": "甲子", "hour": "辛酉"},
            "male",
            id="solitary-phoenix",
        ),
    ),
)
def test_each_v2_extension_has_a_positive_and_negative_fixture(
    name: str,
    positive_pillars: dict[str, str],
    positive_gender: str,
    expected_pillar: str,
    negative_pillars: dict[str, str],
    negative_gender: str,
) -> None:
    positive = calculate_shen_sha(positive_pillars, gender=positive_gender)
    negative = calculate_shen_sha(negative_pillars, gender=negative_gender)

    assert name in positive[expected_pillar]
    assert all(name not in pillar_matches for pillar_matches in negative.values())


def test_other_gender_does_not_guess_a_yuan_chen_direction() -> None:
    pillars = {"year": "甲子", "month": "辛未", "day": "丙寅", "hour": "庚子"}

    result = calculate_shen_sha(pillars, gender="other")

    assert all("元辰" not in pillar_matches for pillar_matches in result.values())
