"""Conversation state management for the Humbug application."""

from typing import Dict, List, Optional

from humbug.conversation.message import Message
from humbug.conversation.message_source import MessageSource
from humbug.conversation.usage import Usage

class ConversationHistory:
    """Manages the conversation history and state."""

    def __init__(self, conversation_id: str):
        """Initialize empty conversation history."""
        self._conversation_id = conversation_id
        self._messages: List[Message] = []
        self._last_response_tokens = {"input": 0, "output": 0}

    def add_message(self, message: Message) -> None:
        """Add a message to the history."""
        self._messages.append(message)

    def update_message(
        self,
        message_id: str,
        content: str,
        usage: Optional[Usage] = None,
        completed: bool = None
    ) -> Optional[Message]:
        """Update an existing message and return the updated message."""
        for message in self._messages:
            if message.id == message_id:
                message.content = content
                if usage is not None:
                    old_usage = message.usage
                    message.usage = usage
                    # Only update token counts if we didn't have usage before
                    if old_usage is None:
                        self._last_response_tokens["input"] = usage.prompt_tokens
                        self._last_response_tokens["output"] = usage.completion_tokens
                if completed is not None:
                    message.completed = completed
                return message
        return None

    def get_messages(self) -> List[Message]:
        """Get a copy of all messages in the conversation history.

        Returns:
            List[Message]: Copy of all messages
        """
        return self._messages.copy()

    def get_messages_for_context(self) -> List[str]:
        """
        Get messages formatted for AI context.

        Returns:
            List of message dictionaries with role and content.
        """
        result = []
        i = 0
        while i < len(self._messages):
            if self._messages[i].source == MessageSource.USER:
                # Found a user message, look for corresponding AI response
                user_msg = self._messages[i]
                ai_msg = None
                if i + 1 < len(self._messages) and self._messages[i + 1].source == MessageSource.AI:
                    ai_msg = self._messages[i + 1]

                # Only include the exchange if:
                # 1. It's a user message without an AI response yet (current exchange)
                # 2. Or it's a completed exchange without errors
                if (ai_msg is None) or (ai_msg.completed and not ai_msg.error):
                    result.append({
                        "role": "user",
                        "content": user_msg.content
                    })
                    if ai_msg:
                        result.append({
                            "role": "assistant",
                            "content": ai_msg.content
                        })
                        i += 1  # Skip the AI message since we've handled it

            i += 1  # Move to next message

        return result

    def get_token_counts(self) -> Dict[str, int]:
        """Get token counts from last response."""
        return self._last_response_tokens

    def update_last_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Update token counts for the last response.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        """
        self._last_response_tokens = {
            "input": input_tokens,
            "output": output_tokens
        }
