"""Provider selection and initialization for AI backends."""

from typing import Dict, Optional

from humbug.ai.ai_backend import AIBackend
from humbug.ai.openai_backend import OpenAIBackend
from humbug.ai.gemini_backend import GeminiBackend


class AIProvider:
    """Factory and manager for AI backends."""

    @staticmethod
    def create_backends(openai_key: Optional[str] = None, google_key: Optional[str] = None) -> Dict[str, AIBackend]:
        """Create AI backends based on available API keys.

        Args:
            openai_key: Optional OpenAI API key
            google_key: Optional Google API key

        Returns:
            Dictionary mapping provider names to backend instances
        """
        backends = {}
        if openai_key:
            backends["openai"] = OpenAIBackend(openai_key)
        if google_key:
            backends["google"] = GeminiBackend(google_key)
        return backends
