# order_state.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import re


class OrderLine(BaseModel):
    """Single item in the order"""
    partnum: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    delivery_date: Optional[str] = None  # per-line delivery date


class OrderState(BaseModel):
    """
    Current state of the order being built.
    delivery_date lives in each OrderLine, not at order level.
    missing_fields is now a structured list of {line_index, field} dicts.
    """
    intent: str = "ORDER"
    customer_name: Optional[str] = None
    customer_company: Optional[str] = None
    order_lines: List[OrderLine] = Field(default_factory=lambda: [OrderLine()])
    is_complete: bool = False
    missing_fields: List[Dict[str, Any]] = Field(default_factory=list)
    # structured format:
    #   {"line_index": None,  "field": "customer_name"}   ← order-level
    #   {"line_index": 0,     "field": "delivery_date"}   ← line-level
    order_status: str = "new"  # new | in_progress | completed | cancelled

    @property
    def active_line_index(self) -> int:
        """
        Index of the line currently being collected.
        Derived from the first line-level entry in missing_fields.
        Falls back to 0 if no line-level missing fields exist.
        """
        for m in self.missing_fields:
            if m.get("line_index") is not None:
                return m["line_index"]
        return 0

    def to_dict(self) -> dict:
        data = self.model_dump()
        # Expose active_line_index so the LLM system prompt can use it directly
        data["active_line_index"] = self.active_line_index
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "OrderState":
        # Back-compat: old top-level delivery_date → push to line[0]
        if "delivery_date" in data and data.get("delivery_date"):
            lines = data.get("order_lines", [{}])
            if lines and not lines[0].get("delivery_date"):
                lines[0]["delivery_date"] = data["delivery_date"]
            data["order_lines"] = lines
        data.pop("delivery_date", None)

        # Back-compat: old flat string missing_fields → convert to structured
        raw_missing = data.get("missing_fields", [])
        if raw_missing and isinstance(raw_missing[0], str):
            data["missing_fields"] = _migrate_missing_fields(raw_missing)

        # active_line_index is computed, never stored
        data.pop("active_line_index", None)

        return cls(**data)

    def update_missing_fields(self):
        """
        Recalculate missing fields in structured format.
        Order-level fields → line_index=None
        Per-line fields   → line_index=<idx>
        """
        missing = []

        # ── Order-level ──────────────────────────────────────────────────
        if not self.customer_name:
            missing.append({"line_index": None, "field": "customer_name"})
        if not self.customer_company:
            missing.append({"line_index": None, "field": "customer_company"})

        # ── Per-line ─────────────────────────────────────────────────────
        for idx, line in enumerate(self.order_lines):
            if not line.product_name:
                missing.append({"line_index": idx, "field": "product_name"})
            if not line.quantity:
                missing.append({"line_index": idx, "field": "quantity"})
            if not line.unit:
                missing.append({"line_index": idx, "field": "unit"})
            if not line.delivery_date:
                missing.append({"line_index": idx, "field": "delivery_date"})

        has_partial_data = (
            self.customer_name
            or self.customer_company
            or any(
                line.product_name or line.quantity or line.unit or line.delivery_date
                for line in self.order_lines
            )
        )

        self.missing_fields = missing
        self.is_complete = len(missing) == 0

        if self.is_complete and len(self.order_lines) > 0:
            if self.order_status in ("new", "in_progress"):
                self.order_status = "in_progress"
        elif has_partial_data:
            if self.order_status == "new":
                self.order_status = "in_progress"
        else:
            self.order_status = "new"

        return missing


def _migrate_missing_fields(flat: list) -> list:
    """Convert old flat string format to structured dicts (back-compat)."""
    structured = []
    for entry in flat:
        if "order_lines[" in entry:
            m = re.match(r"order_lines\[(\d+)\]\.(.+)", entry)
            if m:
                structured.append({"line_index": int(m.group(1)), "field": m.group(2)})
        else:
            structured.append({"line_index": None, "field": entry})
    return structured