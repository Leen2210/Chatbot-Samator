# entity_extractor.py
# src/core/entity_extractor.py
from src.services.llm_service import llm_service
from src.models.intent_result import ExtractedEntities
from src.models.order_state import OrderState
from src.config.prompts.extraction_prompt import (
    ENTITY_EXTRACTION_SYSTEM_PROMPT,
    build_entity_user_prompt,
)
import json
import re


class EntityExtractor:
    """Extracts order entities. Called by OrderAgent only."""

    def __init__(self):
        self.llm_service = llm_service

    def extract(
        self,
        user_message: str,
        current_order_state: OrderState,
        history: list = None,
        edit_mode: bool = False,
    ) -> ExtractedEntities:
        """
        Extract entities from user message.

        Returns:
            ExtractedEntities object (all fields null if nothing found)
        """
        user_prompt = build_entity_user_prompt(
            user_message=user_message,
            current_order_state=current_order_state.to_dict(),
            history=history,
            edit_mode=edit_mode,
        )

        try:
            response = self.llm_service.chat(
                user_message=user_prompt,
                system_prompt=ENTITY_EXTRACTION_SYSTEM_PROMPT,
            )
            return self._parse_entities(response)

        except Exception as e:
            print(f"Error in entity extraction: {e}")
            return ExtractedEntities()

    def _parse_entities(self, response: str) -> ExtractedEntities:
        try:
            cleaned = re.sub(r'^```json\s*', '', response.strip())
            cleaned = re.sub(r'^```\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

            data = json.loads(cleaned)
            return ExtractedEntities(**data)

        except (json.JSONDecodeError, Exception) as e:
            print(f"Failed to parse entities: {e}")
            return ExtractedEntities()
        
        


# Singleton
entity_extractor = EntityExtractor()