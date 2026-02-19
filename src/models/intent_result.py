# src/models/intent_result.py
from pydantic import BaseModel, Field
from typing import Optional, List


class ExtractedOrderLine(BaseModel):
    """Entities extracted for a single order line."""
    line_index: Optional[int] = None      # which existing line to update (0-based); None = new line
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    delivery_date: Optional[str] = None   # per-line delivery date (YYYY-MM-DD)

    def has_any(self) -> bool:
        return any(v is not None for v in [
            self.product_name, self.quantity, self.unit, self.delivery_date
        ])


class ExtractedEntities(BaseModel):
    """
    Result of entity extraction from user message.
    Top-level fields are order-level; order_lines holds per-item data.
    """
    customer_name: Optional[str] = None
    customer_company: Optional[str] = None
    cancellation_reason: Optional[str] = None
    order_lines: List[ExtractedOrderLine] = Field(default_factory=list)

    def has_any(self) -> bool:
        top_level = any(v is not None for v in [
            self.customer_name, self.customer_company, self.cancellation_reason
        ])
        lines = any(line.has_any() for line in self.order_lines)
        return top_level or lines

    def has_new_entities(self, order_state) -> bool:
        """Check if extracted entities differ from current order state."""
        if self.customer_name and self.customer_name != order_state.customer_name:
            return True
        if self.customer_company and self.customer_company != order_state.customer_company:
            return True
        if self.cancellation_reason:
            return True

        for extracted_line in self.order_lines:
            idx = extracted_line.line_index
            if idx is not None and idx < len(order_state.order_lines):
                current = order_state.order_lines[idx]
            elif order_state.order_lines:
                current = order_state.order_lines[0]
            else:
                return True  # new line being added

            if extracted_line.product_name and extracted_line.product_name != current.product_name:
                return True
            if extracted_line.quantity and extracted_line.quantity != current.quantity:
                return True
            if extracted_line.unit and extracted_line.unit != current.unit:
                return True
            if extracted_line.delivery_date and extracted_line.delivery_date != current.delivery_date:
                return True

        return False


class IntentResult(BaseModel):
    """Result from intent classification + entity extraction"""
    intent: str = "UNKNOWN"
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    confidence: float = 1.0
    raw_response: Optional[str] = None

    def has_entities(self) -> bool:
        return self.entities.has_any()

    def has_new_entities(self, order_state) -> bool:
        return self.entities.has_new_entities(order_state)