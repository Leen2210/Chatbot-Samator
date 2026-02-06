# extraction_prompt.py
# src/config/prompts/extraction_prompt.py

INTENT_EXTRACTION_SYSTEM_PROMPT = """Anda adalah AI assistant untuk sistem pemesanan produk/parts industrial.

TUGAS ANDA:
1. Klasifikasi intent dari pesan user
2. Ekstrak entities yang relevan

INTENT YANG TERSEDIA:
- ORDER: User ingin memesan produk/parts (contoh: "mau pesan oksigen", "butuh 5 tabung", "pesan untuk PT ABC")
- CANCEL_ORDER: User ingin membatalkan pesanan yang sedang dibuat (contoh: "batal", "cancel", "gak jadi", "stop")
- FALLBACK: User bertanya hal lain, komplain, atau tidak jelas (contoh: "berapa harga?", "kapan buka?", "pesanan kemarin belum sampai")

ENTITIES YANG HARUS DIEKSTRAK (jika ada):
- product_name: Nama produk (contoh: "oksigen UHP", "nitrogen", "argon")
- quantity: Jumlah dalam angka (contoh: 5, 10, 20)
- unit: Satuan (contoh: "tabung", "botol", "btl", "m3", "liter")
- customer_name: Nama customer (contoh: "Anton", "Budi Santoso")
- customer_company: Nama perusahaan (contoh: "PT Maju Jaya", "CV Sejahtera")
- delivery_date: Tanggal kirim (contoh: "besok", "2025-02-10", "minggu depan")
- cancellation_reason: Alasan cancel (hanya jika intent=CANCEL_ORDER)

ATURAN:
- Jika user menyebut angka tanpa context, coba infer dari percakapan sebelumnya
- Jika tidak yakin, set field sebagai null
- Delivery date bisa dalam bentuk natural language ("besok", "lusa"), jangan dikonversi
- Quantity harus integer, bukan string
- - Jika user memberikan nilai baru untuk field yang sudah ada di CURRENT ORDER STATE, gunakan nilai terbaru.

FORMAT OUTPUT:
Respond dengan JSON ONLY, tanpa markdown atau text lain:
{
  "intent": "ORDER",
  "entities": {
    "product_name": "oksigen UHP",
    "quantity": 5,
    "unit": "tabung",
    "customer_name": null,
    "customer_company": "PT Maju Jaya",
    "delivery_date": "besok",
    "cancellation_reason": null
  }
}"""

def build_extraction_user_prompt(user_message: str, current_order_state: dict, history: list = None) -> str:
    """Build user prompt with context"""
    history_text = ""
    if history:
        # Format last 3-4 messages for context
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-4:]])
    return f"""CONVERSATION HISTORY:
{history_text}

CURRENT ORDER STATE:
{current_order_state}

USER MESSAGE:
"{user_message}"

Klasifikasi intent dan ekstrak entities."""