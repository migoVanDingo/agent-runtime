import openai
from providers.openai_compat import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(api_key=api_key)
