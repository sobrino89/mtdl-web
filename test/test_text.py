import pytest

from mtdl_web.text import fold, index_text, normalize, search_text

# All strings are taken verbatim from the film list of 12.10.2026.


def test_dash_variants_compare_equal() -> None:
    mdr = "Das Gipfeltreffen – Best of 2023 (1/2)"
    sat3 = "Das Gipfeltreffen - Best of 2023 (1/2)"
    assert mdr != sat3
    assert normalize(mdr) == normalize(sat3) == sat3


def test_case_only_differences_fold_equal() -> None:
    assert fold("Extra 3") == fold("extra 3")
    assert normalize("Extra 3") != normalize("extra 3")


def test_ellipsis_and_no_break_space() -> None:
    assert normalize("Aktenzeichen XY… Ungelöst") == "Aktenzeichen XY... Ungelöst"
    assert normalize("Folge\N{NO-BREAK SPACE}1:  Ich bin\n ein Dorfbewohner ") == "Folge 1: Ich bin ein Dorfbewohner"


def test_umlauts_survive() -> None:
    assert fold("Werwölfe · Das Spiel von List und Täuschung") == "werwölfe · das spiel von list und täuschung"


@pytest.mark.parametrize(
    ("text", "query", "index"),
    [  # words of the film list and the rules agreed on 13.10.2026
        ("Köln", "köln", "köln"),
        ("KOELN", "koeln", "koeln"),  # "oe" is not "ö"
        ("Café", "cafe", "cafe"),  # accents go
        ("Israël", "israel", "israel"),
        (chr(0x130) + "stanbul", "istanbul", "istanbul"),
        ("Ångström", "angström", "angström"),
        ("Werwölfe", "werwölfe", "werwölfe"),
        ("Maß halten", "maß halten", "maß mass mas halten"),  # "Mass" and "Mas" find it, "Maß" only "ß" words
        ("Größten", "größten", "größten grössten grösten"),
        ("Das Gipfeltreffen", "das gipfeltreffen", "das gipfeltreffen"),
    ],
)
def test_search_and_index_text(text: str, query: str, index: str) -> None:
    assert (search_text(text), index_text(text)) == (query, index)
