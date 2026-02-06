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

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer)
    status = Column(String, default="pending")
    items = Column(JSON)  # To store a list of products
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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

