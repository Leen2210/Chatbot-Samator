# src/config/prompts/extraction_prompt.py
from datetime import datetime

# ─────────────────────────────────────────────
#  PROMPT 1: Intent Classification (lightweight)
# ─────────────────────────────────────────────

INTENT_CLASSIFICATION_SYSTEM_PROMPT = """Anda adalah classifier intent untuk sistem pemesanan produk industrial.

TUGAS: Klasifikasi intent dari pesan user. Return JSON only.

=== INTENT ===
- ORDER: Memesan produk, menyebut nama produk, quantity, atau informasi pesanan
  * "mau pesan oksigen", "butuh 5 tabung", "liquid n2 bisa?", "ada nitrogen?"
  * Jika pesan mengandung nama produk industrial + kata tanya (bisa/ada/tersedia) → ORDER
- CANCEL_ORDER: Membatalkan pesanan aktif ("batal", "cancel", "gak jadi", "stop")
- CHIT_CHAT: Greeting, courtesy, acknowledgment
  * "terima kasih", "halo", "oke", "tidak ada lagi", "sudah cukup", "baik pak"
  * Jika user bilang "tidak ada" setelah ditanya "ada yang bisa dibantu?" → CHIT_CHAT
- FALLBACK: Pertanyaan non-order yang perlu call center
  * "berapa harga?", "kapan buka?", "komplain produk", "pesanan belum sampai"

=== FORMAT OUTPUT ===
JSON only, tanpa markdown:
{
  "intent": "ORDER"
}"""


def build_intent_user_prompt(user_message: str, history: list = None) -> str:
    history_text = ""
    if history:
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-4:]])

    return f"""CONVERSATION HISTORY:
{history_text}

USER MESSAGE:
"{user_message}"

Klasifikasi intent."""


# ─────────────────────────────────────────────
#  PROMPT 2: Entity Extraction (ORDER only)
# ─────────────────────────────────────────────

ENTITY_EXTRACTION_SYSTEM_PROMPT = """Anda adalah entity extractor untuk sistem pemesanan produk industrial.

TUGAS: Ekstrak entities dari pesan user TERBARU saja.

=== ENTITIES ===
- product_name: Nama produk
- quantity: Jumlah yang dipesan (integer)
- unit: Satuan quantity
- customer_name: Nama individu (Jessica, Budi Santoso)
- customer_company: Nama organisasi (PT/CV/RS/Yayasan/Toko/UD/dll)
- delivery_date: Format YYYY-MM-DD (konversi "besok"=+1 hari, "lusa"=+2 hari dari CURRENT_DATE)
- cancellation_reason: Alasan cancel (hanya untuk CANCEL_ORDER)

=== ATURAN PALING PENTING ===
**HANYA ekstrak entities yang DISEBUTKAN SECARA EKSPLISIT dalam USER MESSAGE terbaru.**
**JANGAN pernah mengulang atau meng-copy nilai dari CURRENT ORDER STATE ke entities.**
**Jika user tidak menyebut produk → product_name harus null.**
**Jika user tidak menyebut jumlah → quantity harus null.**
**CURRENT ORDER STATE hanya sebagai KONTEKS, bukan sumber nilai entities.**

=== MODE EDIT (saat user mengubah pesanan yang sudah lengkap) ===
Jika EDIT_MODE: true, user sedang mengubah field pesanan yang sudah ada.
- "ubah tanggal jadi besok" → delivery_date = besok, sisanya null
- "ganti perusahaan jadi CV ABC" → customer_company = "CV ABC", sisanya null
- "ubah jumlah jadi 10" → quantity = 10, sisanya null

=== ATURAN EKSTRAKSI ===

**1. TABUNG/BOTOL (Gas kemasan)**
"3 tabung oksigen 6m3" → product_name="oksigen 6m3", quantity=3, unit="tabung"
"5 botol nitrogen 10m3" → product_name="nitrogen 10m3", quantity=5, unit="botol"

**2. LIQUID/CURAH (Bulk)**
"liquid oxygen 10000 m3" → product_name="liquid oxygen", quantity=10000, unit="m3"
"nitrogen cair 5000 liter" → product_name="nitrogen cair", quantity=5000, unit="liter"

**3. PARTS/PRODUK LAIN**
Ikuti pola normal (regulator, valve, hose)

=== FORMAT OUTPUT ===
JSON only, tanpa markdown:
{
  "product_name": null,
  "quantity": null,
  "unit": null,
  "customer_name": null,
  "customer_company": null,
  "delivery_date": null,
  "cancellation_reason": null
}"""


def build_entity_user_prompt(
    user_message: str,
    current_order_state: dict,
    history: list = None,
    edit_mode: bool = False,
) -> str:
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_day_id = {
        "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
        "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"
    }.get(now.strftime("%A"), now.strftime("%A"))

    history_text = ""
    if history:
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-4:]])

    return f"""CURRENT_DATE: {current_date} ({current_day_id})
EDIT_MODE: {"true" if edit_mode else "false"}

CONVERSATION HISTORY (konteks saja, jangan copy ke entities):
{history_text}

CURRENT ORDER STATE (konteks saja, jangan copy ke entities):
{current_order_state}

USER MESSAGE TERBARU (hanya ini yang harus diekstrak):
"{user_message}"

Ekstrak HANYA informasi yang ada dalam USER MESSAGE TERBARU. Set semua field yang tidak disebutkan ke null."""