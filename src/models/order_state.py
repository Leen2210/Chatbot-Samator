# order_state.py
# src/models/order_state.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import date

class OrderLine(BaseModel):
    """Single item in the order"""
    partnum: Optional[str] = None  # Part number from database (e.g., BULIN0010000000)
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None  # btl, tabung, m3, etc.
    
    def is_liquid(self) -> bool:
        """Check if product is liquid (doesn't require quantity)"""
        if not self.product_name:
            return False
        name_lower = self.product_name.lower()
        return "liquid" in name_lower or "cair" in name_lower

class OrderState(BaseModel):
    """
    Current state of the order being built
    This gets updated incrementally as conversation progresses
    """
    intent: str = "ORDER"  # ORDER, INQUIRY, COMPLAINT, etc.
    customer_name: Optional[str] = None
    customer_company: Optional[str] = None
    delivery_date: Optional[str] = None  # ISO format: YYYY-MM-DD
    delivery_date_raw: Optional[Dict] = None  # Temporal JSON schema
    order_lines: List[OrderLine] = Field(default_factory=lambda: [OrderLine()])
    is_complete: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    order_status: str = "new"  # new | in_progress | completed | cancelled

    
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
            # Skip quantity validation for liquid products
            if not line.is_liquid() and not line.quantity:
                missing.append(f"order_lines[{idx}].quantity")
            if not line.unit:
                missing.append(f"order_lines[{idx}].unit")

        if len(missing) == 0 and len(self.order_lines) > 0:
            self.is_complete = True
            if self.order_status == "new" or self.order_status == "in_progress":
                self.order_status = "in_progress"  # Ready for confirmation
        elif any(
            line.product_name or line.quantity or line.unit
            for line in self.order_lines
        ) or self.customer_name or self.customer_company:            # Has some data → in_progress
            if self.order_status == "new":
                self.order_status = "in_progress"
        else:
            # No data yet → remains new
            self.order_status = "new"
                
        self.missing_fields = missing
        self.is_complete = len(missing) == 0
        
        return missing