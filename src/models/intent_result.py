# src/models/intent_result.py
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class ExtractedEntities(BaseModel):
    """Entities extracted from user message"""
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    customer_name: Optional[str] = None
    customer_company: Optional[str] = None
    delivery_date: Optional[str] = None  # Will be normalized later
    cancellation_reason: Optional[str] = None  # For CANCEL_ORDER intent

    def has_any(self) -> bool:
        """Check if any entity was extracted."""
        return any(v is not None for v in self.model_dump().values())

class IntentResult(BaseModel):
    """Result from intent classification + entity extraction"""
    intent: str = "UNKNOWN"  # ORDER, CANCEL_ORDER, FALLBACK, UNKNOWN
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    confidence: float = 1.0  # 0.0 to 1.0
    raw_response: Optional[str] = None  # For debugging
    
    def has_entities(self) -> bool:
        """Check if any entities were extracted"""
        entities_dict = self.entities.model_dump(exclude_none=True)
        return len(entities_dict) > 0
    
    def has_new_entities(self, order_state) -> bool:
        """
        Check if entities contain values that are NEW compared to current order state.
        Prevents acting on LLM echo of existing state.
        """
        e = self.entities
        current_line = order_state.order_lines[0] if order_state.order_lines else None

        if e.product_name and e.product_name != (current_line.product_name if current_line else None):
            return True
        if e.quantity and e.quantity != (current_line.quantity if current_line else None):
            return True
        if e.unit and e.unit != (current_line.unit if current_line else None):
            return True
        if e.customer_name and e.customer_name != order_state.customer_name:
            return True
        if e.customer_company and e.customer_company != order_state.customer_company:
            return True
        if e.delivery_date and e.delivery_date != order_state.delivery_date:
            return True
        if e.cancellation_reason:
            return True

        return False