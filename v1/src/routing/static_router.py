from sklearn.metrics.pairwise import cosine_similarity
from shared_types import RoutingRule
from embeddings import get_embedding_model
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

class StaticRouter:

    def __init__(self, registry):
        self._rules: list[RoutingRule] = registry.get_all_rules()
        self._toolset_embeddings = None  # lazy-loaded on first select()
        logger.info(f"Static router initialized ({len(self._rules)} rules)")

    def _ensure_embeddings(self):
        if self._toolset_embeddings is not None:
            return
        model = get_embedding_model()
        self._toolset_embeddings = {
            name: model.encode(desc, show_progress_bar=False)
            for name, desc in config.routing.toolset_descriptions.items()
        }

    def select(self, message: str, history: list[dict]) -> list[str]:
        toolsets: set[str] = set()

        # Heuristic rules — owned by each toolset definition
        for rule in self._rules:
            if rule.condition(message, history):
                toolsets.add(rule.toolset)

        # Embedding-based routing — one encode per call
        self._ensure_embeddings()
        model = get_embedding_model()
        threshold = config.routing.embedding_threshold
        msg_emb = model.encode(message, show_progress_bar=False)
        for name, toolset_emb in self._toolset_embeddings.items():
            score = float(cosine_similarity([msg_emb], [toolset_emb])[0][0])
            if score > threshold:
                toolsets.add(name)
                logger.info(f"Embedding match: {name} (score={score:.3f})")

        if not toolsets:
            default = config.routing.default_toolsets
            logger.info(f"No signals detected — fallback to {default}")
            return list(default)

        result = sorted(toolsets)
        logger.info(f"Static router selected: {result}")
        return result
