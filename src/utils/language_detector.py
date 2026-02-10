# src/utils/language_detector.py
"""
Language detection utility for bilingual chatbot (English/Indonesian)
"""

class LanguageDetector:
    """Detect language from user input"""
    
    # Common English words that are NOT commonly used in Indonesian
    ENGLISH_INDICATORS = {
        'the', 'is', 'are', 'was', 'were', 'have', 'has', 'had', 'will', 'would',
        'can', 'could', 'should', 'may', 'might', 'must', 'shall',
        'this', 'that', 'these', 'those', 'what', 'which', 'who', 'where', 'when',
        'how', 'why', 'please', 'thank', 'thanks', 'hello', 'hi', 'good', 'morning',
        'afternoon', 'evening', 'night', 'order', 'need', 'want', 'like', 'get',
        'make', 'take', 'give', 'tell', 'ask', 'work', 'seem', 'feel', 'try',
        'leave', 'call', 'delivery', 'product', 'company', 'customer', 'date'
    }
    
    # Common Indonesian words
    INDONESIAN_INDICATORS = {
        'saya', 'anda', 'kamu', 'kami', 'mereka', 'dia', 'ini', 'itu', 'yang', 'dan',
        'atau', 'untuk', 'dari', 'ke', 'di', 'pada', 'dengan', 'adalah', 'akan',
        'sudah', 'belum', 'tidak', 'bukan', 'jangan', 'mau', 'ingin', 'butuh',
        'bisa', 'boleh', 'harus', 'perlu', 'ada', 'apa', 'siapa', 'dimana', 'kapan',
        'bagaimana', 'kenapa', 'mengapa', 'tolong', 'terima', 'kasih', 'halo', 'hai',
        'selamat', 'pagi', 'siang', 'sore', 'malam', 'pesan', 'pesanan', 'kirim',
        'tanggal', 'nama', 'perusahaan', 'produk', 'barang'
    }
    
    @staticmethod
    def detect(text: str) -> str:
        """
        Detect language from text
        
        Args:
            text: Input text
            
        Returns:
            'en' for English, 'id' for Indonesian
        """
        if not text or not text.strip():
            return 'id'  # Default to Indonesian
        
        # Normalize text
        words = text.lower().split()
        
        # Count indicators
        english_count = sum(1 for word in words if word in LanguageDetector.ENGLISH_INDICATORS)
        indonesian_count = sum(1 for word in words if word in LanguageDetector.INDONESIAN_INDICATORS)
        
        # Decision logic
        if english_count > indonesian_count:
            return 'en'
        elif indonesian_count > 0:
            return 'id'
        else:
            # No clear indicators - check for English sentence patterns
            text_lower = text.lower()
            if any(pattern in text_lower for pattern in ['i want', 'i need', 'can i', 'could you', 'please', 'thank you']):
                return 'en'
            return 'id'  # Default to Indonesian

# Singleton
language_detector = LanguageDetector()
