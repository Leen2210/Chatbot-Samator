# src/core/intent_classifier.py
from src.services.llm_service import llm_service
from src.config.prompts.extraction_prompt import (
    INTENT_CLASSIFICATION_SYSTEM_PROMPT,
    build_intent_user_prompt,
)
import json
import re


class IntentClassifier:
    """Classifies intent only. Entity extraction is handled by OrderAgent."""

    VALID_INTENTS = {"ORDER", "CANCEL_ORDER", "CHIT_CHAT", "FALLBACK"}

    def __init__(self):
        self.llm_service = llm_service

    def classify(self, user_message: str, history: list = None) -> str:
        """
        Classify intent from user message.

        Returns:
            Intent string: ORDER | CANCEL_ORDER | CHIT_CHAT | FALLBACK | UNKNOWN
        """
        user_prompt = build_intent_user_prompt(
            user_message=user_message,
            history=history,
        )

        try:
            response = self.llm_service.chat(
                user_message=user_prompt,
                system_prompt=INTENT_CLASSIFICATION_SYSTEM_PROMPT,
            )
            return self._parse_intent(response)

        except Exception as e:
            print(f"Error in intent classification: {e}")
            return "FALLBACK"

    def _parse_intent(self, response: str) -> str:
        try:
            cleaned = re.sub(r'^```json\s*', '', response.strip())
            cleaned = re.sub(r'^```\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

            data = json.loads(cleaned)
            intent = data.get("intent", "UNKNOWN").upper()
            return intent if intent in self.VALID_INTENTS else "UNKNOWN"

        except json.JSONDecodeError:
            return self._extract_intent_from_text(response)

    def _extract_intent_from_text(self, text: str) -> str:
        text_lower = text.lower()
        if any(w in text_lower for w in ["order", "pesan", "beli"]):
            return "ORDER"
        elif any(w in text_lower for w in ["cancel", "batal", "stop"]):
            return "CANCEL_ORDER"
        elif any(w in text_lower for w in ["chit_chat", "greeting"]):
            return "CHIT_CHAT"
        elif any(w in text_lower for w in ["fallback", "redirect"]):
            return "FALLBACK"
        return "UNKNOWN"


# Singleton
intent_classifier = IntentClassifier()