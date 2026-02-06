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