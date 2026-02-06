# intent_classifier.py
# src/core/intent_classifier.py
from src.services.llm_service import llm_service
from src.models.intent_result import IntentResult, ExtractedEntities
from src.models.order_state import OrderState
from src.config.prompts.extraction_prompt import (
    INTENT_EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt
)
import json
import re

class IntentClassifier:
    """
    Handles intent classification AND entity extraction in a single LLM call
    """
    
    def __init__(self):
        self.llm_service = llm_service
    
    def classify_and_extract(self, user_message: str, current_order_state: OrderState, history: list = None) -> IntentResult:
        """
        Single LLM call to classify intent and extract entities
        
        Args:
            user_message: User's message
            current_order_state: Current state of the order
        
        Returns:
            IntentResult with intent and extracted entities
        """
        # Build the prompt
        user_prompt = build_extraction_user_prompt(
            user_message=user_message,
            current_order_state=current_order_state.to_dict(), 
            history=history
        )
        
        try:
            # Call LLM
            response = self.llm_service.chat(
                user_message=user_prompt,
                system_prompt=INTENT_EXTRACTION_SYSTEM_PROMPT
            )
            
            # Parse JSON response
            result = self._parse_llm_response(response)
            
            # Validate and return
            return result
        
        except Exception as e:
            print(f"Error in intent classification: {e}")
            # Return fallback result
            return IntentResult(
                intent="FALLBACK",
                entities=ExtractedEntities(),
                confidence=0.0,
                raw_response=str(e)
            )
    
    def _parse_llm_response(self, response: str) -> IntentResult:
        """
        Parse LLM response into IntentResult
        
        Args:
            response: Raw LLM response (should be JSON)
        
        Returns:
            IntentResult object
        """
        try:
            # Clean up response - remove markdown code blocks if present
            cleaned_response = response.strip()
            
            # Remove ```json and ``` if present
            cleaned_response = re.sub(r'^```json\s*', '', cleaned_response)
            cleaned_response = re.sub(r'^```\s*', '', cleaned_response)
            cleaned_response = re.sub(r'\s*```$', '', cleaned_response)
            
            # Parse JSON
            data = json.loads(cleaned_response)
            
            # Extract intent
            intent = data.get("intent", "UNKNOWN").upper()
            
            # Validate intent
            valid_intents = ["ORDER", "CANCEL_ORDER", "FALLBACK"]
            if intent not in valid_intents:
                intent = "UNKNOWN"
            
            # Extract entities
            entities_data = data.get("entities", {})
            entities = ExtractedEntities(**entities_data)
            
            return IntentResult(
                intent=intent,
                entities=entities,
                confidence=1.0,  # High confidence if JSON parsed successfully
                raw_response=response
            )
        
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON from LLM: {e}")
            print(f"Raw response: {response}")
            
            # Try to extract intent from text as fallback
            intent = self._extract_intent_from_text(response)
            
            return IntentResult(
                intent=intent,
                entities=ExtractedEntities(),
                confidence=0.5,  # Low confidence
                raw_response=response
            )
        
        except Exception as e:
            print(f"Unexpected error parsing response: {e}")
            return IntentResult(
                intent="UNKNOWN",
                entities=ExtractedEntities(),
                confidence=0.0,
                raw_response=response
            )
    
    def _extract_intent_from_text(self, text: str) -> str:
        """
        Fallback: Try to extract intent from non-JSON text
        
        Args:
            text: Raw text response
        
        Returns:
            Intent string (ORDER, CANCEL_ORDER, FALLBACK, or UNKNOWN)
        """
        text_lower = text.lower()
        
        if any(word in text_lower for word in ["order", "pesan", "beli"]):
            return "ORDER"
        elif any(word in text_lower for word in ["cancel", "batal", "stop"]):
            return "CANCEL_ORDER"
        elif any(word in text_lower for word in ["fallback", "redirect", "other"]):
            return "FALLBACK"
        else:
            return "UNKNOWN"

# Singleton
intent_classifier = IntentClassifier()