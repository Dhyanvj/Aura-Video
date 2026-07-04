import time
from typing import List, Optional, Type, TypeVar, Union

import anthropic
from loguru import logger
from pydantic import BaseModel, ValidationError

from app.config import config
from app.db import session_scope
from app.db.models import AgentEvent, VideoProject

T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output). Unknown models fall back to the Sonnet 5 rate.
_MODEL_PRICING = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_DEFAULT_PRICING = _MODEL_PRICING["claude-sonnet-5"]

_TOOL_NAME = "emit_result"


class AgentNotConfiguredError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(config.agents.get("anthropic_api_key"))


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class BaseAgent:
    """
    Shared plumbing for every LLM-backed agent: Anthropic calls, structured JSON
    output validated against a Pydantic model, retry with backoff, cost
    accounting, and AgentEvent logging. Producer/Orchestrator are not LLM
    agents and do not subclass this.
    """

    agent_name = "agent"

    def __init__(self, project_id: Optional[int] = None):
        self.project_id = project_id
        self.model = config.agents.get("model", "claude-sonnet-5")
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            api_key = config.agents.get("anthropic_api_key")
            if not api_key:
                raise AgentNotConfiguredError(
                    "agents.anthropic_api_key is not configured in config.toml"
                )
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def log_event(
        self,
        type_: str,
        message: str = "",
        payload: Optional[dict] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        if self.project_id is None:
            return
        with session_scope() as session:
            session.add(
                AgentEvent(
                    project_id=self.project_id,
                    agent=self.agent_name,
                    type=type_,
                    message=message,
                    payload=payload,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                )
            )
            if cost_usd:
                project = session.get(VideoProject, self.project_id)
                if project is not None:
                    project.cost_usd = (project.cost_usd or 0.0) + cost_usd
                    session.add(project)
            session.commit()

    def call_json(
        self,
        system: str,
        user: str,
        response_model: Type[T],
        max_retries: int = 3,
        max_tokens: int = 4096,
    ) -> T:
        """Call Claude with a plain-text user turn and force a structured response."""
        return self.call_json_with_content(system, user, response_model, max_retries, max_tokens)

    def call_json_with_content(
        self,
        system: str,
        user: Union[str, List[dict]],
        response_model: Type[T],
        max_retries: int = 3,
        max_tokens: int = 4096,
    ) -> T:
        """
        Call Claude and force a structured response matching response_model,
        via a single-tool tool_choice. `user` may be a plain string or a list
        of content blocks (e.g. mixed text/image blocks for vision review).
        Retries on transient API errors and on pydantic validation failures
        (feeding the error back to the model).
        """
        if not is_configured():
            raise AgentNotConfiguredError(
                "agents.anthropic_api_key is not configured in config.toml"
            )

        tool = {
            "name": _TOOL_NAME,
            "description": f"Emit the result as {response_model.__name__}.",
            "input_schema": response_model.model_json_schema(),
        }

        messages = [{"role": "user", "content": user}]
        last_error: Optional[str] = None

        for attempt in range(1, max_retries + 1):
            self.log_event(
                "thinking",
                message=f"Calling {self.model} (attempt {attempt}/{max_retries})",
            )
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": _TOOL_NAME},
                )
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                last_error = str(exc)
                logger.warning(f"{self.agent_name}: API error on attempt {attempt}: {exc}")
                self._sleep_backoff(attempt)
                continue

            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = estimate_cost_usd(self.model, tokens_in, tokens_out)

            tool_use = next(
                (b for b in response.content if b.type == "tool_use" and b.name == _TOOL_NAME),
                None,
            )
            if tool_use is None:
                last_error = f"model did not call {_TOOL_NAME} (stop_reason={response.stop_reason})"
                self.log_event(
                    "error", message=last_error, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost
                )
                self._sleep_backoff(attempt)
                continue

            try:
                result = response_model.model_validate(tool_use.input)
            except ValidationError as exc:
                last_error = str(exc)
                self.log_event(
                    "error",
                    message=f"validation failed: {last_error}",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                )
                # Feed the validation error back so the model can self-correct.
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": f"Invalid input: {last_error}. Please retry.",
                                "is_error": True,
                            }
                        ],
                    }
                )
                continue

            self.log_event(
                "output",
                message=f"{self.agent_name} produced a valid {response_model.__name__}",
                payload=tool_use.input,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )
            return result

        raise RuntimeError(f"{self.agent_name}: failed after {max_retries} attempts: {last_error}")

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 20))
