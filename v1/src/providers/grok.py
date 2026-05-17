import openai
from providers.openai_compat import OpenAICompatibleProvider


class GrokProvider(OpenAICompatibleProvider):
    """xAI Grok — OpenAI-compatible API at api.x.ai."""

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
