"""Small non-financial MCP Tool provider used by the local runtime demonstration."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from app import __version__


class RuntimeCapabilities(BaseModel):
    runtime: str
    version: str
    guarantees: list[str]


class TextProfile(BaseModel):
    characters: int = Field(ge=0)
    words: int = Field(ge=0)
    lines: int = Field(ge=0)
    contains_code_fence: bool


mcp_tool_provider = MCPServer(
    name="aurumlab-runtime-tools",
    title="AurumLab Runtime Tool Provider",
    description="Bounded non-financial utilities for MCP Client discovery and governance demos.",
    version=__version__,
)


@mcp_tool_provider.tool(structured_output=True)
def describe_runtime_capabilities() -> RuntimeCapabilities:
    """Return deterministic Agent runtime capability metadata."""
    return RuntimeCapabilities(
        runtime="AurumLab",
        version=__version__,
        guarantees=[
            "versioned_skills",
            "bounded_plans",
            "governed_tools",
            "schema_pinned_mcp",
        ],
    )


@mcp_tool_provider.tool(structured_output=True)
def profile_text(text: Annotated[str, Field(max_length=2_000)]) -> TextProfile:
    """Return bounded structural statistics for text without interpreting instructions."""
    if len(text) > 2_000:
        raise ValueError("text must not exceed 2000 characters")
    return TextProfile(
        characters=len(text),
        words=len(text.split()),
        lines=text.count("\n") + 1,
        contains_code_fence="```" in text,
    )


def main() -> None:
    mcp_tool_provider.run(transport="stdio")


if __name__ == "__main__":
    main()
