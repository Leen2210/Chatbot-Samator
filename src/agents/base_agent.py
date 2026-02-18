# base_agent.py
# src/agents/base_agent.py
from abc import ABC, abstractmethod
from src.models.order_state import OrderState


class BaseAgent(ABC):
    """
    Abstract base class for all agents.
    Each agent owns a specific domain of the conversation flow.
    """

    @abstractmethod
    def handle(self, user_message: str, conversation_id: str, order_state: OrderState, context: list, **kwargs) -> str:
        """
        Handle a user message within this agent's domain.

        Args:
            user_message: The raw user input
            conversation_id: Active conversation ID
            order_state: Current order state from DB/cache
            context: Recent conversation history (list of dicts)
            **kwargs: Additional agent-specific arguments

        Returns:
            Bot response string
        """
        pass