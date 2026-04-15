import openai
from providers.openai_compat import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):

    def __init__(self, base_url: str, model: str):
        self.model = model
        self.client = openai.OpenAI(base_url=base_url, api_key="ollama")
