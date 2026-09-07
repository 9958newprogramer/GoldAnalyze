import pytest

from app.tools.search import TavilySearchProvider


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.tavily.com", "https://api.tavily.com/search"),
        ("https://api.tavily.com/search", "https://api.tavily.com/search"),
    ],
)
def test_tavily_provider_accepts_host_or_search_endpoint(base_url, expected):
    provider = TavilySearchProvider("test-key", base_url)

    assert provider.endpoint == expected


def test_tavily_provider_rejects_non_https_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        TavilySearchProvider("test-key", "http://api.tavily.com")
