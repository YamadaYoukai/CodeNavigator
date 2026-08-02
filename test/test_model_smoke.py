import os

import pytest

from evaluation.run_model_smoke import configure_no_proxy_for_base_url


def test_configure_no_proxy_preserves_existing_entries_and_adds_api_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,example.test")
    monkeypatch.setenv("no_proxy", "localhost")

    configure_no_proxy_for_base_url("http://192.0.2.10/openai/v1")

    assert os.environ["NO_PROXY"].split(",") == [
        "localhost",
        "example.test",
        "192.0.2.10",
    ]
    assert os.environ["no_proxy"].split(",") == ["localhost", "192.0.2.10"]


def test_configure_no_proxy_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "api.example.test")
    monkeypatch.delenv("no_proxy", raising=False)

    configure_no_proxy_for_base_url("https://api.example.test/v1")
    configure_no_proxy_for_base_url("https://api.example.test/v1")

    assert os.environ["NO_PROXY"] == "api.example.test"
    assert os.environ["no_proxy"] == "api.example.test"


@pytest.mark.parametrize("base_url", ["", "not-a-url", "/v1"])
def test_configure_no_proxy_rejects_base_url_without_host(base_url: str) -> None:
    with pytest.raises(RuntimeError, match="must include a hostname"):
        configure_no_proxy_for_base_url(base_url)
