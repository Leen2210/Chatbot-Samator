# semantic_search_service.py
"""
Semantic Search Service for finding parts using vector embeddings
Uses BGE-M3 model for multilingual embeddings
"""

import numpy as np
from typing import List, Dict, Optional
from src.services.cache_service import cache_store
from src.services.sql_service import sql_service
from src.database.sql_schema import Parts

# Import BGE-M3 model
try:
    from sentence_transformers import SentenceTransformer
    BGE_MODEL_AVAILABLE = True
except ImportError:
    BGE_MODEL_AVAILABLE = False
    print("⚠️  sentence-transformers not installed. Install with: pip install sentence-transformers")


class SemanticSearchService:
    """
    Service for semantic search of parts using BGE-M3 embeddings
    Model: BAAI/bge-m3 (Multilingual, supports Indonesian and English)
    """

    def __init__(self):
        self.sql_service = sql_service
        self.cache = cache_store
        self._parts_cache = None
        self._embedding_model = None

        # Load BGE-M3 model on initialization
        if BGE_MODEL_AVAILABLE:
            self._load_embedding_model()

    def _load_embedding_model(self):
        """Load BGE-M3 embedding model"""
        if self._embedding_model is None:
            try:
                print("🔄 Loading BGE-M3 embedding model...")
                self._embedding_model = SentenceTransformer('BAAI/bge-m3')
                print("✅ BGE-M3 model loaded successfully!")
            except Exception as e:
                print(f"❌ Failed to load BGE-M3 model: {e}")
                print("   Falling back to fuzzy search only")
                self._embedding_model = None
    
    def search_part_by_description(self, query: str, top_k: int = 3, threshold: float = 0.5) -> List[Dict]:
        """
        Search parts using semantic similarity
        
        Args:
            query: User's product description (e.g., "oksigen cair", "nitrogen gas")
            top_k: Number of top results to return
            threshold: Minimum similarity score (0.0 to 1.0)
        
        Returns:
            List of matched parts with similarity scores, sorted by similarity (highest first)
        """
        # 1. Generate embedding for query
        query_embedding = self._generate_embedding(query)
        
        if query_embedding is None:
            return []
        
        # 2. Get all parts with embeddings
        all_parts = self._get_all_parts()
        
        if not all_parts:
            return []
        
        # 3. Calculate cosine similarity for each part
        similarities = []
        for part in all_parts:
            if part.get('embedding') is None:
                continue
            
            score = self._cosine_similarity(query_embedding, part['embedding'])
            
            # Only include if above threshold
            if score >= threshold:
                similarities.append({
                    'id': part['id'],
                    'partnum': part['partnum'],
                    'description': part['description'],
                    'uom': part['uom'],
                    'uomdesc': part['uomdesc'],
                    'similarity': float(score)
                })
        
        # 4. Sort by similarity (highest first)
        similarities.sort(key=lambda x: x['similarity'], reverse=True)
        
        # 5. Return top K
        return similarities[:top_k]
    
    def _generate_embedding(self, text: str) -> Optional[np.ndarray]:
        """
        Generate embedding for text query using BGE-M3 model

        BGE-M3 is a multilingual embedding model that supports:
        - Indonesian language
        - English language
        - 1024-dimensional embeddings

        Args:
            text: Text to embed (e.g., "oksigen cair", "liquid nitrogen")

        Returns:
            Embedding vector as numpy array (1024-dim for BGE-M3)
        """
        # Check if model is available
        if self._embedding_model is None:
            print("⚠️  BGE-M3 model not available, falling back to fuzzy search")
            return None

        try:
            # Generate embedding using BGE-M3
            # Note: BGE-M3 produces 1024-dimensional embeddings
            embedding = self._embedding_model.encode(
                text,
                normalize_embeddings=True  # Normalize for cosine similarity
            )

            return embedding.astype(np.float32)

        except Exception as e:
            print(f"❌ Error generating embedding: {e}")
            return None
    
    def _get_all_parts(self) -> List[Dict]:
        """
        Get all parts with embeddings from cache or database
        
        Returns:
            List of parts with embeddings
        """
        # Try to get from cache first
        if self._parts_cache is not None:
            return self._parts_cache
        
        # Get from database
        try:
            db = self.sql_service.db
            parts = db.query(Parts).all()
            
            parts_list = []
            for part in parts:
                parts_list.append({
                    'id': part.id,
                    'partnum': part.partnum,
                    'description': part.description,
                    'uom': part.uom,
                    'uomdesc': part.uomdesc,
                    'embedding': part.embedding  # This is already a Python list
                })
            
            # Cache for future use
            self._parts_cache = parts_list
            
            return parts_list
        
        except Exception as e:
            print(f"Error loading parts: {e}")
            return []
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors
        
        Args:
            vec1: First vector (numpy array)
            vec2: Second vector (list of floats)
        
        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        try:
            # Convert vec2 to numpy array
            vec2_np = np.array(vec2, dtype=np.float32)
            
            # Calculate cosine similarity
            dot_product = np.dot(vec1, vec2_np)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2_np)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            similarity = dot_product / (norm1 * norm2)
            
            # Ensure result is between 0 and 1
            return max(0.0, min(1.0, float(similarity)))
        
        except Exception as e:
            print(f"Error calculating similarity: {e}")
            return 0.0
    
    def search_by_partnum(self, partnum: str) -> Optional[Dict]:
        """
        Search part by exact part number
        
        Args:
            partnum: Part number to search
        
        Returns:
            Part details or None
        """
        try:
            db = self.sql_service.db
            part = db.query(Parts).filter(Parts.partnum == partnum).first()
            
            if part:
                return {
                    'id': part.id,
                    'partnum': part.partnum,
                    'description': part.description,
                    'uom': part.uom,
                    'uomdesc': part.uomdesc
                }
            
            return None
        
        except Exception as e:
            print(f"Error searching by partnum: {e}")
            return None
    
    def fuzzy_search_by_description(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Fuzzy text search by description (fallback when embeddings not available)
        
        Args:
            query: Search query
            top_k: Number of results
        
        Returns:
            List of matched parts
        """
        query_lower = query.lower()
        all_parts = self._get_all_parts()
        
        matches = []
        for part in all_parts:
            desc_lower = part['description'].lower()
            
            # Simple substring matching
            if query_lower in desc_lower or desc_lower in query_lower:
                matches.append({
                    'id': part['id'],
                    'partnum': part['partnum'],
                    'description': part['description'],
                    'uom': part['uom'],
                    'uomdesc': part['uomdesc'],
                    'match_type': 'fuzzy'
                })
        
        return matches[:top_k]


# Singleton instance
semantic_search_service = SemanticSearchService()

