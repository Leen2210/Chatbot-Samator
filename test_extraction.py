import asyncio
# Impor yang benar sesuai repositori Anda
from src.core.intent_classifier import intent_classifier
from src.models.order_state import OrderState

async def debug_extraction():
    user_message = "saya mau beli tabung oksigen 6m3 UHP"
    # Orchestrator selalu mengirimkan state saat ini (bisa kosong)
    current_order_state = OrderState()
    
    print(f"--- Debug Intent & Entity Extraction ---")
    print(f"Pesan User: {user_message}")
    
    # Fungsi ini yang dipanggil oleh orchestrator.py di baris 119
    intent_result = intent_classifier.classify_and_extract(user_message, current_order_state)
    
    print(f"Intent Terdeteksi: {intent_result.intent}")
    print(f"Entitas Ter ekstrak: {intent_result.entities.model_dump()}")
    
    # Ini adalah string yang dikirim ke Semantic Search di orchestrator.py baris 248
    extracted_product = intent_result.entities.product_name
    print(f"\nString yang dikirim ke Semantic Search: '{extracted_product}'")

if __name__ == "__main__":
    # IntentClassifier di kode Anda bukan async, jadi bisa langsung dipanggil
    asyncio.run(debug_extraction())