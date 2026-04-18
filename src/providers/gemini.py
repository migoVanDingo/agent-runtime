import openai
from providers.openai_compat import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
    """Google Gemini via the OpenAI-compatible endpoint at generativelanguage.googleapis.com."""

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
