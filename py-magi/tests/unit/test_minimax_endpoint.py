from litellm.llms.minimax.chat.transformation import MinimaxChatConfig

from providers.client import HOSTS


def test_minimax_routes_use_the_openai_chat_endpoint() -> None:
    # The minimax prefix is LiteLLM's OpenAI-compatible route. An Anthropic
    # base makes it POST /anthropic/v1/chat/completions, which MiniMax answers
    # with "404 page not found".
    expected = {
        "minimax-cn": "https://api.minimaxi.com/v1/chat/completions",
        "minimax-global": "https://api.minimax.io/v1/chat/completions",
    }
    config = MinimaxChatConfig()
    seen: set[str] = set()
    for host in HOSTS:
        if host.id not in expected:
            continue
        seen.add(host.id)
        url = config.get_complete_url(
            api_base=host.api_base,
            api_key="key",
            model=host.default_model,
            optional_params={},
            litellm_params={},
        )
        assert url == expected[host.id]
    assert seen == set(expected)
