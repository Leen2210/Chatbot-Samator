# cache_service.py
# src/services/cache_service.py

class CacheService:
    def __init__(self):
        # Our in-memory store
        self._cache = {}

    def get(self, key: str):
        """Retrieve data from memory"""
        return self._cache.get(key)

    def set(self, key: str, value: any):
        """Store data in memory"""
        self._cache[key] = value

    def exists(self, key: str) -> bool:
        return key in self._cache

    def clear(self):
        self._cache = {}

    def get_customer(self, phone_number: str):
        """Get customer from cache"""
        return self._cache.get(f"customer:{phone_number}")
    
    def set_customer(self, phone_number: str, customer_data: dict, ttl: int = 86400):
        """Cache customer data (TTL: 24h = 86400s)"""
        self._cache[f"customer:{phone_number}"] = customer_data
        # Note: In-memory dict doesn't support TTL, use Redis later
    
    # Conversation Context Cache
    def get_conversation_context(self, conversation_id: str):
        """Get recent messages from cache"""
        return self._cache.get(f"context:{conversation_id}")
    
    def set_conversation_context(self, conversation_id: str, messages: list):
        """Cache last N messages for context"""
        self._cache[f"context:{conversation_id}"] = messages
    
    # Product Cache (you already have this via warm_up_cache)
    def get_product(self, product_key: str):
        """Get product from cache"""
        return self._cache.get(f"product:{product_key}")
    
    # ORDER STATE CACHE - NEW
    def get_order_state(self, conversation_id: str) -> dict:
        """Get current order state from cache"""
        return self._cache.get(f"order_state:{conversation_id}")
    
    def set_order_state(self, conversation_id: str, order_state: dict):
        """Cache current order state (TTL: 2h for active orders)"""
        self._cache[f"order_state:{conversation_id}"] = order_state
    
    def delete_order_state(self, conversation_id: str):
        """Clear order state (when order completed or cancelled)"""
        key = f"order_state:{conversation_id}"
        if key in self._cache:
            del self._cache[key]

# Create a singleton instance to be used across the app
cache_store = CacheService()