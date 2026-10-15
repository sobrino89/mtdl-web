from mtdl_web.models import film_id

# MDR, "Das Gipfeltreffen", episode 58, as in the film list of 12.10.2026.
URL_58 = (
    "https://mdr-progressive.ard-mcdn.de/mdr/video/weltweit/2026/07/23/"
    "mdrde-38127fc0-8c32-4404-b778-51ebad4e26f8.avc720.mp4"
)


def test_film_id_is_stable() -> None:
    # The value is stored in databases and must never change.
    assert film_id("MDR", "Das Gipfeltreffen", "Das Beste kommt noch! (58)", URL_58) == 9113043070428711758


def test_film_id_separates_fields() -> None:
    assert film_id("A", "BC", "D", "u") != film_id("AB", "C", "D", "u")


def test_version_is_the_same_everywhere() -> None:
    import tomllib
    from pathlib import Path

    from mtdl_web import USER_AGENT, __version__

    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert USER_AGENT.startswith(f"mtdl-web/{__version__} ")
