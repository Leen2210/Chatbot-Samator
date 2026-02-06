# orchestrator.py
from src.services.sql_service import SQLService, SessionLocal
from src.database.sql_schema import Customer, Parts
from src.services.cache_service import cache_store
from src.services.sql_service import sql_service
from src.services.llm_service import llm_service
from src.core.conversation_manager import conversation_manager
import json


class Orchestrator:
    def __init__(self):
        self.cache_service = cache_store
        self.sql_service = sql_service
        self.llm_service = llm_service 
        self.conversation_manager = conversation_manager
        
        self.current_conversation_id = None

        self.warm_up_cache()

        # We will initialize agents and services here later
        pass

    def start_conversation(self, phone_number: str):
        """Initialize conversation for a user"""
        self.current_conversation_id = self.conversation_manager.get_or_create_conversation(phone_number)
        return self.current_conversation_id

    def handle_message(self, user_message: str) -> str:
        """
        Handle incoming user message
        
        Args:
            user_message: The message from the user
        
        Returns:
            Bot's response
        """

        # 1. Store user message to DB
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='user',
            content=user_message
        )

        # 2. Get current order state from cache
        current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        
        # 3. Get conversation context from cache/DB
        context = self.conversation_manager.get_context(self.current_conversation_id)

        # Simple integration test - just send to OpenAI and return response
        # 4. Build system prompt with order state
        system_prompt = f"""You are a helpful order-taking assistant for a parts/products store.

Current Order State:
{json.dumps(current_order_state.to_dict(), indent=2)}

Instructions:
- Help the user complete their order
- Be friendly and concise
- Ask for missing information: {', '.join(current_order_state.missing_fields) if current_order_state.missing_fields else 'None'}
"""

        # 5. Generate response with context
        response = self.llm_service.chat(
            user_message=user_message,
            system_prompt=system_prompt,
            conversation_history=context[:-1]  # Exclude current message
        )
        
        # 6. Store bot response to DB
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='assistant',
            content=response
        )
        
        
        return response
    
    def get_current_order_state(self) -> dict:
        """Get current order state as dict (for debugging/display)"""
        if not self.current_conversation_id:
            return {}
        
        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        return order_state.to_dict()
    
    def warm_up_cache(self):
        print("Warming up cache with customer data...")
        db = SessionLocal()
        parts = db.query(Parts).all()
        for c in parts:
            cache_store.set(c.id, {"id": c.id, "partnum": c.partnum, "description": c.description, "uom": c.uom, "embedding": c.embedding})
        db.close()
        print(f"Cache ready with {len(parts)} records.")