import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


class AgentOutputParseError(ValueError):
    """Raised when an Agent response cannot be parsed into the shared schema."""


def parse_agent_output(output_model: type[OutputModelT], raw_output: str | dict[str, Any]) -> OutputModelT:
    if isinstance(raw_output, str):
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise AgentOutputParseError(f"Agent output is not valid JSON: {exc}") from exc
    else:
        payload = raw_output

    try:
        return output_model.model_validate(payload)
    except ValidationError as exc:
        raise AgentOutputParseError(f"Agent output does not match {output_model.__name__}: {exc}") from exc
