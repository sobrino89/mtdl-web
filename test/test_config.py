import pytest

from mtdl_web.config import Settings


def test_description_index_defaults_to_on() -> None:
    assert Settings.from_env({}).index_description is True


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("0", False), ("OFF", False), (" yes ", True)])
def test_description_index_flag(value: str, expected: bool) -> None:
    assert Settings.from_env({"MTDL_INDEX_DESCRIPTION": value}).index_description is expected


def test_invalid_flag_is_an_error() -> None:
    with pytest.raises(ValueError, match="MTDL_INDEX_DESCRIPTION"):
        Settings.from_env({"MTDL_INDEX_DESCRIPTION": "vielleicht"})


def test_hooks_live_in_the_config_folder() -> None:
    settings = Settings.from_env({"MTDL_CONFIG_DIR": "/data/mtdl", "MTDL_HOOK_TIMEOUT": "120"})
    assert (str(settings.hooks_dir), settings.hook_timeout) == ("/data/mtdl/hooks", 120)
    assert Settings.from_env({}).hook_timeout == 600
    with pytest.raises(ValueError, match="MTDL_HOOK_TIMEOUT"):
        Settings.from_env({"MTDL_HOOK_TIMEOUT": "0"})
