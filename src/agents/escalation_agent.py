# src/agents/escalation_agent.py
from src.agents.base_agent import BaseAgent
from src.models.order_state import OrderState


class EscalationAgent(BaseAgent):
    """
    Handles FALLBACK intent.
    Redirects out-of-scope questions to the call center.
    No LLM needed — deterministic response.
    """

    REDIRECT_ID = (
        "Maaf, untuk bantuan atau pertanyaan tersebut silakan hubungi "
        "customer service kami di [Nomor Telepon]. "
        "Ada lagi yang bisa saya bantu terkait pemesanan?"
    )

    REDIRECT_EN = (
        "Sorry, for that assistance or question, please contact our customer service "
        "at [Phone Number]. "
        "Is there anything else I can help you with regarding orders?"
    )

    def handle(self, user_message: str, conversation_id: str, order_state: OrderState, context: list, **kwargs) -> str:
        language = kwargs.get('language', 'id')
        return self.REDIRECT_EN if language == 'en' else self.REDIRECT_ID


escalation_agent = EscalationAgent()