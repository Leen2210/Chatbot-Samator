# src/agents/escalation_agent.py
from src.agents.base_agent import BaseAgent
from src.models.order_state import OrderState


class EscalationAgent(BaseAgent):

    def handle(self, user_message: str, conversation_id: str, order_state: OrderState, context: list, **kwargs) -> str:
        """Entry point dari orchestrator — redirect ke call center."""
        language = kwargs.get("language", "id")
        return self.handle_redirect(language)

    def handle_redirect(self, language: str = "id") -> str:
        if language == "en":
            return (
                "Sorry, for that assistance or question, please contact our customer service "
                "at [Phone Number].\n\n"
                'If you want to return to the bot, type "back to bot".'
            )
        return (
            "Maaf, untuk bantuan atau pertanyaan tersebut silakan hubungi "
            "customer service kami di [Nomor Telepon].\n\n"
            'Jika Anda ingin kembali dengan bot, silahkan ketikan "balik ke bot".'
        )

    def handle_handoff_message(self, language: str = "id") -> str:
        if language == "en":
            return 'Please wait for a response from our call center. To continue with me, type "back to bot".'
        return 'Mohon menunggu balasan dari call center kami. Jika ingin melanjutkan dengan saya, silahkan ketikan "balik ke bot".'


escalation_agent = EscalationAgent()