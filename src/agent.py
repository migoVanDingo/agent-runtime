from messenger import Messenger
from tools.registry import ToolRegistry
from tools.toolsets import ALL_TOOLSETS
from routing.static_router import StaticRouter
from runtime.classifier import WorkflowSelector
from runtime.critic import PlanCritic
from runtime.entity_critic import EntityCritic
from runtime.guard import ActionGuard
from runtime.escalation import CLIUserGate
from runtime.monitor import ExecutionMonitor
from runtime.context_manager import ContextManager
from runtime.importance import ImportanceScorer
from runtime.pipeline import Pipeline
from runtime.pipeline_context import PipelineContext
from runtime.validator import PlanValidator
from planning.planner import Planner
from planning.synthesizer import Synthesizer
from workflows.matcher import WorkflowMatcher
from runtime.stages.routing import RoutingStage, DirectInlineStage
from runtime.stages.workflow_match import WorkflowMatchStage
from runtime.stages.planning import PlanningStage
from runtime.stages.entity_critic import EntityCriticStage
from runtime.stages.validator import ValidatorStage
from runtime.stages.council import CouncilStage
from runtime.stages.execution import ExecutionStage
from runtime.stages.synthesizer import SynthesizerStage
from runtime.stages.direct_execution import DirectExecutionStage
from providers.factory import get_provider, get_runtime_provider
from ui.spinner import Spinner
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


def _build_pipeline(agent: "Agent") -> Pipeline:
    """Assemble the ordered stage pipeline from agent dependencies."""
    p = agent
    system = config.agent.system_prompt

    direct_execution = DirectExecutionStage(
        provider=p.provider,
        registry=p.registry,
        router=p.router,
        context_mgr=p.context_mgr,
        messenger=p.messenger,
        guard=p.guard,
        user_gate=p.user_gate,
        spinner=p.spinner,
        agent_system=system,
    )

    stages = [
        RoutingStage(
            provider=p.provider,
            context_mgr=p.context_mgr,
            workflow_matcher=p.workflow_matcher,
            messenger=p.messenger,
        ),
        DirectInlineStage(messenger=p.messenger),
        WorkflowMatchStage(
            workflow_matcher=p.workflow_matcher,
            workflow_selector=p.workflow_selector,
            spinner=p.spinner,
        ),
        PlanningStage(
            planner=p.planner,
            validator=p.validator,
            spinner=p.spinner,
        ),
        EntityCriticStage(entity_critic=p.entity_critic),
        ValidatorStage(),
        CouncilStage(
            critic=p.critic,
            planner=p.planner,
            validator=p.validator,
            spinner=p.spinner,
        ),
        ExecutionStage(
            provider=p.provider,
            registry=p.registry,
            router=p.router,
            context_mgr=p.context_mgr,
            messenger=p.messenger,
            monitor=p.monitor,
            guard=p.guard,
            user_gate=p.user_gate,
            importance_scorer=p.importance_scorer,
            planner=p.planner,
            spinner=p.spinner,
            agent_system=system,
        ),
        SynthesizerStage(synthesizer=p.synthesizer, spinner=p.spinner),
        direct_execution,
    ]

    return Pipeline(
        stages=stages,
        fallback_stage=direct_execution,
        user_input_fn=input,
    )


class Agent:

    def __init__(self, verbose: bool = False, user_gate=None, initial_messages: list[dict] | None = None):
        self.provider = get_provider()
        self.messenger = Messenger()
        if initial_messages:
            self.messenger.get_messages().extend(initial_messages)
        self.registry = ToolRegistry()
        self.spinner = Spinner(verbose=verbose)

        for toolset in ALL_TOOLSETS:
            self.registry.register_toolset(toolset)

        self.router = StaticRouter(self.registry)
        self.context_mgr = ContextManager()
        self.context_mgr.set_summarizer(get_runtime_provider())
        self.workflow_selector = WorkflowSelector(get_runtime_provider())
        self.validator = PlanValidator(set(self.registry.toolset_names()), self.registry.tool_names())
        self.critic = PlanCritic(self.registry)
        self.guard = ActionGuard()
        self.user_gate = user_gate or CLIUserGate()
        self.monitor = ExecutionMonitor(get_runtime_provider())
        self.importance_scorer = ImportanceScorer(get_runtime_provider())
        self.planner = Planner(self.provider)
        self.synthesizer = Synthesizer(self.provider)
        self.workflow_matcher = WorkflowMatcher()
        self.entity_critic = EntityCritic()
        self._recall_injected = False
        self._last_response = ""

        self._pipeline = _build_pipeline(self)

        # Pre-warm the embedding model so the first user message has no cold-start delay.
        from embeddings import get_embedding_model
        get_embedding_model()

    @property
    def last_response(self) -> str:
        return self._last_response

    def _build_startup_recall_block(self, query: str) -> str | None:
        try:
            from runtime.artifact_store import get_artifact_store

            store = get_artifact_store()
        except Exception:
            return None

        rag_cfg = config.artifact_store.rag
        threshold = float(rag_cfg.similarity_threshold)
        top_k = int(rag_cfg.top_k)
        budget = max(300, int(rag_cfg.max_injected_chars))

        try:
            sessions = store.recall_sessions(query, top_k=top_k, threshold=threshold)
            artifacts = store.recall_artifacts(query, top_k=top_k, threshold=threshold)
        except Exception as e:
            logger.warning(f"startup recall skipped: {e}")
            return None

        if not sessions and not artifacts:
            return None

        lines: list[str] = ["[Prior related work]"]
        if sessions:
            lines.append("Sessions:")
            for s in sessions:
                excerpt = s.summary.replace("\n", " ").strip()
                if len(excerpt) > 160:
                    excerpt = excerpt[:157] + "..."
                lines.append(f"- ({s.score:.2f}) {s.session_id}: {excerpt}")
        if artifacts:
            lines.append("Artifacts:")
            for a in artifacts:
                excerpt = a.summary.replace("\n", " ").strip()
                if len(excerpt) > 140:
                    excerpt = excerpt[:137] + "..."
                lines.append(f"- ({a.score:.2f}) {a.key} [{a.kind}]: {excerpt}")
        lines.append("[/Prior related work]")

        out = "\n".join(lines)
        if len(out) > budget:
            out = out[: budget - 20].rstrip() + "\n[/Prior related work]"
        return out

    def call(self, user_message: str) -> str:
        from runtime.utils import banner
        logger.info(banner("User"))
        logger.info(f"  {user_message}")
        effective_user_message = user_message
        if (
            config.artifact_store.enabled
            and config.artifact_store.rag.enabled
            and config.artifact_store.rag.inject_on_start
            and not self._recall_injected
        ):
            block = self._build_startup_recall_block(user_message)
            if block:
                effective_user_message = f"{user_message}\n\n{block}"
                logger.info("startup recall: injected prior related work context")
            self._recall_injected = True

        self.messenger.add_user_message(user_message)
        self.spinner.start("Thinking...")

        context = PipelineContext(user_message=effective_user_message)
        response = self._pipeline.run(context)
        self.spinner.stop()  # guaranteed cleanup — stages that exit early (e.g. DirectInlineStage DONE) may not stop it

        # Tier 2: log request for workflow discovery if artifact store is active.
        try:
            from runtime.artifact_store import get_artifact_store

            get_artifact_store().record_request(user_message, workflow=context.workflow_name)
        except Exception:
            pass

        logger.info(banner("Assistant"))
        logger.info(f"  {response}")
        self._last_response = response
        return response
