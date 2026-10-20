import pytest
from src.config import manager
from src.config.settings.staging import BackendStageSettings


@pytest.mark.parametrize(
    "value,expected",
    [
        ("DEV", "dev"),
        ("development", "dev"),
        ("STAGE", "stage"),
        ("STAGING", "stage"),
        (" stage ", "stage"),
        ("PROD", "prod"),
        ("production", "prod"),
    ],
)
def test_only_explicit_environment_aliases_select_settings(
    monkeypatch, value, expected
):
    monkeypatch.setattr(manager, "BackendDevSettings", lambda **_: "dev")
    monkeypatch.setattr(manager, "BackendStageSettings", lambda **_: "stage")
    monkeypatch.setattr(manager, "BackendProdSettings", lambda **_: "prod")
    assert manager.BackendSettingsFactory(value)() == expected


@pytest.mark.parametrize("value", ["", " ", "stagin", "test", "PRODD", None, True, 1])
def test_unknown_environment_never_falls_back_to_production(monkeypatch, value):
    monkeypatch.setattr(
        manager,
        "BackendProdSettings",
        lambda: pytest.fail("must not construct production settings"),
    )
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        manager.BackendSettingsFactory(value)()


def test_staging_debug_default_is_off():
    assert BackendStageSettings.model_fields["DEBUG"].default is False


@pytest.mark.parametrize("value", ["STAGING", "staging", " STAGE "])
def test_alias_is_canonicalized_before_pydantic_reads_environment(monkeypatch, value):
    monkeypatch.setenv("ENVIRONMENT", value)
    selected = manager.BackendSettingsFactory(value)()
    assert isinstance(selected, BackendStageSettings)
    assert selected.ENVIRONMENT.value == "STAGE"
    assert selected.DEBUG is False
