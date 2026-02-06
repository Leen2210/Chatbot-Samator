# order_state.py
# src/models/order_state.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date

class OrderLine(BaseModel):
    """Single item in the order"""
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None  # btl, tabung, m3, etc.

class OrderState(BaseModel):
    """
    Current state of the order being built
    This gets updated incrementally as conversation progresses
    """
    intent: str = "ORDER"  # ORDER, INQUIRY, COMPLAINT, etc.
    customer_name: Optional[str] = None
    customer_company: Optional[str] = None
    delivery_date: Optional[str] = None  # ISO format: YYYY-MM-DD
    order_lines: List[OrderLine] = Field(default_factory=lambda: [OrderLine()])
    is_complete: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage"""
        return self.model_dump()
    
    @classmethod
    def from_dict(cls, data: dict) -> 'OrderState':
        """Create from dictionary"""
        return cls(**data)
    
    def update_missing_fields(self):
        """Calculate which required fields are still missing"""
        missing = []
        
        # Check customer info
        if not self.customer_name:
            missing.append("customer_name")
        if not self.customer_company:
            missing.append("customer_company")
        
        # Check delivery date
        if not self.delivery_date:
            missing.append("delivery_date")
        
        # Check order lines
        for idx, line in enumerate(self.order_lines):
            if not line.product_name:
                missing.append(f"order_lines[{idx}].product_name")
            if not line.quantity:
                missing.append(f"order_lines[{idx}].quantity")
            if not line.unit:
                missing.append(f"order_lines[{idx}].unit")
        
        self.missing_fields = missing
        self.is_complete = len(missing) == 0
        
        return missing