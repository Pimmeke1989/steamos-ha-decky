"""Pure helpers of the artwork module."""

from custom_components.steamos.artwork import normalize_title, parse_overrides, shorten_title


def test_normalize_title():
    assert normalize_title("Hades™ II  (Steam)") == "hades ii"
    assert normalize_title("  Baba Is You ") == "baba is you"


def test_shorten_title():
    assert shorten_title("Hades II: Something Else") == "Hades II"
    assert shorten_title("The Witcher 3 - Wild Hunt") == "The Witcher 3"
    assert shorten_title("Celeste") is None


def test_parse_overrides():
    text = "Hades II = 5138\nbroken line\nCeleste=  17 \nNope = abc"
    assert parse_overrides(text) == {"Hades II": 5138, "Celeste": 17}
    assert parse_overrides("") == {}
