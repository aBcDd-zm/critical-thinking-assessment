from __future__ import annotations

import logging

from app.agents.dialogue_llm_client import DialogueLLMClient
from app.agents.mock_dialogue import MockHostAgent
from app.agents.schemas import AgentRuntimeContext, HostOutput
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class HostAgent:
    def __init__(
        self,
        llm_client: DialogueLLMClient | None = None,
        mock_agent: MockHostAgent | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._mock_agent = mock_agent or MockHostAgent()

    @property
    def llm_client(self) -> DialogueLLMClient:
        if self._llm_client is None:
            self._llm_client = DialogueLLMClient()
        return self._llm_client

    def generate(self, context: AgentRuntimeContext) -> HostOutput:
        if get_settings().MODEL_GATEWAY_MODE.lower() == "mock":
            return self._mock_agent.generate(context)

        result = self.llm_client.call_host(context)
        if result.success and isinstance(result.output, HostOutput):
            return result.output

        logger.warning("host agent real model failed: %s", result.error_code)
        fallback = self._mock_agent.generate(context)
        return fallback.model_copy(
            update={
                "fallback_used": True,
                "warnings": fallback.warnings
                + [f"real model failed: {result.error_code or 'UNKNOWN'}"],
            }
        )


__all__ = ["HostAgent"]
