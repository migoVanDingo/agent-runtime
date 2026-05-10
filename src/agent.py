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
from skills.registry import SkillRegistry
from runtime.stages.rag_context import RagContextStage
from runtime.stages.routing import RoutingStage, DirectInlineStage
from runtime.stages.skill_hint import SkillHintStage
from runtime.stages.skill_expansion import SkillExpansionStage
from runtime.stages.planning import PlanningStage
from runtime.stages.entity_critic import EntityCriticStage
from runtime.stages.validator import ValidatorStage
from runtime.stages.council import CouncilStage
from runtime.stages.execution import ExecutionStage
from runtime.stages.continuation import ContinuationStage
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

    skill_expansion = SkillExpansionStage(registry=p.skill_registry)

    execution = ExecutionStage(
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
        skill_expansion=skill_expansion,
    )

    stages = [
        RagContextStage(),
        RoutingStage(
            provider=get_runtime_provider(),  # routing is classification — runtime model is fast enough
            context_mgr=p.context_mgr,
            skill_registry=p.skill_registry,
            messenger=p.messenger,
        ),
        DirectInlineStage(messenger=p.messenger),
        SkillHintStage(
            skill_registry=p.skill_registry,
            skill_selector=p.workflow_selector,
            spinner=p.spinner,
        ),
        PlanningStage(
            planner=p.planner,
            validator=p.validator,
            spinner=p.spinner,
        ),
        skill_expansion,
        EntityCriticStage(entity_critic=p.entity_critic),
        ValidatorStage(),
        CouncilStage(
            critic=p.critic,
            planner=p.planner,
            validator=p.validator,
            spinner=p.spinner,
            skill_expansion_stage=skill_expansion,
        ),
        execution,
        ContinuationStage(
            provider=get_runtime_provider(),
            planner=p.planner,
            execution_stage=execution,
            spinner=p.spinner,
            skill_registry=p.skill_registry,
            skill_expansion_stage=skill_expansion,
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

    def __init__(
        self,
        verbose: bool = False,
        user_gate=None,
        initial_messages: list[dict] | None = None,
        container=None,  # runtime.container.Container — optional; builds its own if absent
    ):
        # Resolve provider and registry either from an injected container or module globals.
        if container is not None:
            self.provider = container.provider
            self.registry = container.registry
            self.router = container.router
        else:
            self.provider = get_provider()
            self.registry = ToolRegistry()
            for toolset in ALL_TOOLSETS:
                self.registry.register_toolset(toolset)
            self.router = StaticRouter(self.registry)

        self.messenger = Messenger()
        if initial_messages:
            self.messenger.get_messages().extend(initial_messages)
        self.spinner = Spinner(verbose=verbose)

        self.context_mgr = ContextManager()
        self.context_mgr.set_summarizer(get_runtime_provider())
        self.workflow_selector = WorkflowSelector(get_runtime_provider())
        self.critic = PlanCritic(self.registry)
        self.guard = ActionGuard()
        self.user_gate = user_gate or CLIUserGate()
        self.skill_registry = SkillRegistry()
        self.validator = PlanValidator(
            set(self.registry.toolset_names()),
            self.registry.tool_names(),
            registered_skills=set(self.skill_registry.names()),
        )
        self.monitor = ExecutionMonitor(
            get_runtime_provider(),
            skill_registry=self.skill_registry,
        )
        self.importance_scorer = ImportanceScorer(get_runtime_provider())
        self.planner = Planner(self.provider)
        self.planner.set_skill_registry(self.skill_registry)
        self.synthesizer = Synthesizer(self.provider)
        self.entity_critic = EntityCritic()
        self._last_response: str = ""
        self._pipeline = _build_pipeline(self)

    @property
    def last_response(self) -> str:
        return self._last_response

    def call(self, user_message: str, on_token=None) -> str:
        """Run the agent pipeline."""
        from runtime.utils import banner
        from runtime.persistence import PersistenceWriter

        logger.info(banner("User"))
        logger.info(f"  {user_message}")

        self.messenger.add_user_message(user_message)
        self.spinner.start("Thinking...")

        # ── Persistence: open session ──────────────────────────────────
        from app_config import config as _config
        db_session_id = PersistenceWriter.start_session(
            original_query=user_message,
            model=getattr(self.provider, "model", _config.llm.model or "unknown"),
            provider=type(self.provider).__name__,
        )

        context = PipelineContext(
            user_message=user_message,
            db_session_id=db_session_id,
            on_token=on_token,
        )
        response = self._pipeline.run(context)
        self.spinner.stop()

        # ── Persistence: close session ─────────────────────────────────
        total_steps = len(context.plan.steps) if context.plan else 0
        PersistenceWriter.finish_session(
            db_session_id or "",
            total_steps=total_steps,
        )

        # Log request for workflow discovery if artifact store is active.
        try:
            from runtime.artifact_store import get_artifact_store

            get_artifact_store().record_request(user_message, workflow=context.workflow_name)
        except Exception:
            pass

        logger.info(banner("Assistant"))
        logger.info(f"  {response}")
        self._last_response = response
        return response
