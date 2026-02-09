# sql_schema.py
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey
from sqlalchemy.dialects.postgresql import ARRAY, REAL
from sqlalchemy.sql import func
from datetime import datetime


# This is the 'Base' that sql_service is looking for
Base = declarative_base()

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(String(50), primary_key=True, index=True)
    customername = Column(String(255))
    customermainphone = Column(String(50))

# src/database/sql_schema.py

class Order(Base):
    __tablename__ = "orders"

    # Existing fields
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(String(50), ForeignKey('customers.id'), nullable=True)  # Changed to String to match Customer.id
    status = Column(String, default="pending")
    items = Column(JSON)  # Keep this! Stores multiple items
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # NEW FIELDS - Add these
    order_id = Column(String, unique=True, index=True)  # User-friendly ID: ORD-20250206-0001
    conversation_id = Column(String, ForeignKey('conversations.id'), nullable=True)

    # Denormalized customer data (for historical record)
    customer_name = Column(String, nullable=True)
    customer_company = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)

    # Order details
    delivery_date = Column(String, nullable=True)  # "2025-02-10" or "besok"
    notes = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Parts(Base): 
    __tablename__ = "parts_embed"

    id = Column(Integer, primary_key=True, index=True)
    partnum = Column(String(50))
    description = Column(Text)
    uom = Column(String(20))
    uomdesc = Column(String(20))
    embedding = Column(ARRAY(REAL))

class Conversation(Base):
    __tablename__ = 'conversations'
    
    id = Column(String, primary_key=True)  # conversation_id
    phone_number = Column(String, nullable=False)
    status = Column(String, default='active')  # active, completed, abandoned
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    order_status = Column(String, default='new')  # new | in_progress | completed | cancelled

    # Store order state as JSON for flexibility
    order_state = Column(JSON, default={})

class Message(Base):
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, ForeignKey('conversations.id'), nullable=False)
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    
    # Optional: Store extracted entities for this message
    entities = Column(JSON, nullable=True)

