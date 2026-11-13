"""The single LLM call and everything that decides whether to trust it (E1, E9).

Only this package and `necropsy/analysis/service.py` may import `LlmClient`.
The narrator runs at pipeline step 8, after the cache lookup has missed, so a
cache hit can never reach a provider (Property 11).
"""

from necropsy.llm.client import FakeLlmClient, LlmClient, LlmError, build_client
from necropsy.llm.prompt import Prompt, build
from necropsy.llm.validate import narrate, validate

__all__ = [
    "FakeLlmClient",
    "LlmClient",
    "LlmError",
    "Prompt",
    "build",
    "build_client",
    "narrate",
    "validate",
]
