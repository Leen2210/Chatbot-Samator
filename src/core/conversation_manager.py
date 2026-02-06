# src/core/conversation_manager.py
from src.services.sql_service import sql_service
from src.services.cache_service import cache_store
from src.database.sql_schema import Conversation, Message
from src.models.order_state import OrderState
from datetime import datetime, timezone
import pytz
import uuid

# Define Indonesian timezone (WIB = UTC+7)
WIB = pytz.timezone('Asia/Jakarta')

def now_wib():
    """Get current time in WIB (Indonesian time)"""
    return datetime.now(WIB)

class ConversationManager:
    """Handles conversation storage and retrieval"""
    
    def __init__(self):
        self.sql_service = sql_service
        self.cache_service = cache_store
    
    def get_or_create_conversation(self, phone_number: str) -> tuple[str, str, dict]:
        """
        Get active conversation or create new one
        
        Returns: 
            tuple: (conversation_id, order_status, last_order_state)
            - order_status: "new" | "in_progress" | "completed" | "cancelled"
            - last_order_state: dict of last incomplete order (if exists)
        """
        # 🆕 First, check for INCOMPLETE orders (in_progress)
        incomplete_conversation = self.sql_service.db.query(Conversation).filter_by(
            phone_number=phone_number,
            order_status='in_progress'  # Only incomplete orders
        ).order_by(Conversation.updated_at.desc()).first()
        
        if incomplete_conversation:
            # Found incomplete order - return for resume logic
            conversation_id = incomplete_conversation.id
            
            # Load order state to cache
            if incomplete_conversation.order_state:
                self.cache_service.set_order_state(conversation_id, incomplete_conversation.order_state)
            
            return (
                conversation_id,
                "in_progress",
                incomplete_conversation.order_state or {}
            )
        
        # 🆕 Check for active conversation (but might be "new" or "completed")
        active_conversation = self.sql_service.db.query(Conversation).filter_by(
            phone_number=phone_number,
            status='active'
        ).order_by(Conversation.updated_at.desc()).first()
        
        if active_conversation:
            conversation_id = active_conversation.id
            
            # Load to cache if not there
            cached_state = self.cache_service.get_order_state(conversation_id)
            if not cached_state:
                if active_conversation.order_state:
                    self.cache_service.set_order_state(conversation_id, active_conversation.order_state)
            
            return (
                conversation_id,
                active_conversation.order_status or "new",
                active_conversation.order_state or {}
            )
        
        # No existing conversation - create new one
        conversation = Conversation(
            id=str(uuid.uuid4()),
            phone_number=phone_number,
            status='active',
            order_status='new',  # 🆕 Set initial status
            order_state={},
            created_at=now_wib(),
            updated_at=now_wib()
        )
        self.sql_service.db.add(conversation)
        self.sql_service.db.commit()
        
        # Initialize empty order state in cache
        initial_state = OrderState()
        self.cache_service.set_order_state(conversation.id, initial_state.to_dict())
        
        return (conversation.id, "new", {})
    
    def add_message(self, conversation_id: str, role: str, content: str, entities: dict = None):
        """
        Store message to database (synchronous for prototype)
        
        Args:
            conversation_id: ID of the conversation
            role: 'user' or 'assistant'
            content: Message content
            entities: Optional extracted entities
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            entities=entities,
            created_at=now_wib()  # WIB time
        )
        self.sql_service.db.add(message)
        
        # Update conversation timestamp
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation:
            conversation.updated_at = now_wib()  # WIB time
        
        self.sql_service.db.commit()
        
        # Update cache with recent messages
        self._update_context_cache(conversation_id)
    
    def _update_context_cache(self, conversation_id: str):
        """Update cache with last 10 messages for context"""
        messages = self.sql_service.db.query(Message).filter_by(
            conversation_id=conversation_id
        ).order_by(Message.created_at.desc()).limit(10).all()
        
        # Reverse to get chronological order
        messages = list(reversed(messages))
        
        context = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        self.cache_service.set_conversation_context(conversation_id, context)
    
    def get_context(self, conversation_id: str, limit: int = 10) -> list:
        """
        Get conversation context (from cache first, DB fallback)
        
        Returns: List of messages [{"role": "user", "content": "..."}]
        """
        # Try cache first
        context = self.cache_service.get_conversation_context(conversation_id)
        if context:
            return context
        
        # Fallback to DB
        messages = self.sql_service.db.query(Message).filter_by(
            conversation_id=conversation_id
        ).order_by(Message.created_at.desc()).limit(limit).all()
        
        messages = list(reversed(messages))
        context = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Update cache
        self.cache_service.set_conversation_context(conversation_id, context)
        
        return context
    
    # ORDER STATE MANAGEMENT
    
    def get_order_state(self, conversation_id: str) -> OrderState:
        """
        Get current order state from cache (fast) or DB (fallback)
        
        Returns: OrderState object
        """
        # Try cache first (fast path)
        cached_state = self.cache_service.get_order_state(conversation_id)
        if cached_state:
            return OrderState.from_dict(cached_state)
        
        # Fallback to DB
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation and conversation.order_state:
            order_state = OrderState.from_dict(conversation.order_state)
            # Update cache
            self.cache_service.set_order_state(conversation_id, order_state.to_dict())
            return order_state
        else:
            # Return empty state
            return OrderState()
    
    def update_order_state(self, conversation_id: str, order_state: OrderState):
        """
        Update order state in both cache and DB
        
        Args:
            conversation_id: ID of the conversation
            order_state: Updated OrderState object
        """
        # Update missing fields
        order_state.update_missing_fields()
        
        order_dict = order_state.to_dict()
        
        # Update cache immediately (fast)
        self.cache_service.set_order_state(conversation_id, order_dict)
        
        # Update DB (slower, but persistent)
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation:
            conversation.order_state = order_dict
            conversation.updated_at = now_wib()  # WIB time
            self.sql_service.db.commit()
    
    def mark_order_complete(self, conversation_id: str):
        """Mark conversation as completed"""
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation:
            conversation.status = 'completed'
            conversation.updated_at = now_wib()  # WIB time
            self.sql_service.db.commit()

    def update_order_state(self, conversation_id: str, order_state: OrderState):
        """
        Update order state in both cache and DB
        Also syncs order_status column for fast queries
        """
        # Update missing fields (this also updates order_status)
        order_state.update_missing_fields()
        
        order_dict = order_state.to_dict()
        
        # Update cache immediately (fast)
        self.cache_service.set_order_state(conversation_id, order_dict)
        
        # Update DB (slower, but persistent)
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation:
            conversation.order_state = order_dict
            
            # 🆕 Sync order_status to separate column for fast queries
            conversation.order_status = order_state.order_status
            
            conversation.updated_at = now_wib()
            self.sql_service.db.commit()

    def mark_order_completed(self, conversation_id: str):
        """
        Mark order as completed (submitted to system)
        This prevents further modifications
        """
        conversation = self.sql_service.db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation:
            # Update order_state
            if conversation.order_state:
                order_state = OrderState.from_dict(conversation.order_state)
                order_state.order_status = "completed"
                conversation.order_state = order_state.to_dict()
            
            # Update status column
            conversation.order_status = "completed"
            conversation.status = "completed"  # Also mark conversation as completed
            conversation.updated_at = now_wib()
            self.sql_service.db.commit()
            
            # Clear from cache (completed orders don't need fast access)
            self.cache_service.delete_order_state(conversation_id)

# Singleton
conversation_manager = ConversationManager()