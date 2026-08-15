"""Composable, run-scoped capabilities for the shared Agent executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .agent_tools import AgentTool


class AgentCapabilityError(RuntimeError):
    """A capability could not prepare or validate one Agent run."""


class AgentCapabilityOutputError(AgentCapabilityError):
    """The final model output violated a capability-owned contract."""


@dataclass(frozen=True)
class AgentCapabilityResult:
    """Metadata and verified artifacts emitted after final-output validation."""

    name: str
    metadata: dict[str, object]
    artifacts: tuple[object, ...] = ()


class AgentCapability(Protocol):
    """One complete Agent ability: prompt contract, tools and output validation."""

    @property
    def name(self) -> str: ...

    def prompt_section(self) -> str: ...

    def tools(self) -> tuple[AgentTool, ...]: ...

    def finalize(self, output_text: str) -> AgentCapabilityResult: ...


class AgentCapabilityRegistry:
    """Validate and compose all capabilities registered for a single Agent run."""

    def __init__(self, capabilities: tuple[AgentCapability, ...] = ()) -> None:
        registered: dict[str, AgentCapability] = {}
        for capability in capabilities:
            name = capability.name.strip()
            if not name:
                raise ValueError("Agent capability name must not be empty")
            if name in registered:
                raise ValueError(f"duplicate Agent capability name: {name}")
            registered[name] = capability
        self._capabilities = tuple(registered.values())

    def apply_prompt(self, base_prompt: str) -> str:
        sections = [
            section
            for capability in self._capabilities
            if (section := capability.prompt_section().strip())
        ]
        if not sections:
            return base_prompt
        return base_prompt.rstrip() + "\n\n" + "\n\n".join(sections)

    def tools(self) -> tuple[AgentTool, ...]:
        return tuple(
            tool
            for capability in self._capabilities
            for tool in capability.tools()
        )

    def finalize(self, output_text: str) -> tuple[AgentCapabilityResult, ...]:
        results = tuple(capability.finalize(output_text) for capability in self._capabilities)
        for capability, result in zip(self._capabilities, results, strict=True):
            if result.name != capability.name:
                raise AgentCapabilityError(
                    f"Agent capability {capability.name} returned a mismatched result"
                )
        return results
