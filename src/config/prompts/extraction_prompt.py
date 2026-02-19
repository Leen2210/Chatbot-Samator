# src/config/prompts/extraction_prompt.py
from datetime import datetime

# ─────────────────────────────────────────────
#  PROMPT 1: Intent Classification (unchanged)
# ─────────────────────────────────────────────

INTENT_CLASSIFICATION_SYSTEM_PROMPT = """Anda adalah classifier intent untuk sistem pemesanan produk industrial.

TUGAS: Klasifikasi intent dari pesan user. Return JSON only.

=== INTENT ===
- ORDER: Memesan produk, menyebut nama produk, quantity, atau informasi pesanan
  * "mau pesan oksigen", "butuh 5 tabung", "liquid n2 bisa?", "ada nitrogen?"
  * Jika pesan mengandung nama produk industrial + kata tanya (bisa/ada/tersedia) → ORDER
  * nama perusahaan, nama orang juga bisa jadi bagian dari ORDER
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
#  PROMPT 2: Entity Extraction — multi-line with per-line delivery_date
# ─────────────────────────────────────────────

ENTITY_EXTRACTION_SYSTEM_PROMPT = """Anda adalah entity extractor untuk sistem pemesanan produk industrial.

TUGAS: Ekstrak entities dari pesan user TERBARU saja.

=== STRUKTUR OUTPUT ===
Kembalikan JSON dengan struktur berikut:

{
  "customer_name": null,
  "customer_company": null,
  "cancellation_reason": null,
  "order_lines": [
    {
      "line_index": null,
      "product_name": null,
      "quantity": null,
      "unit": null,
      "delivery_date": null
    }
  ]
}

- customer_name, customer_company, cancellation_reason: level order (berlaku untuk semua item)
- order_lines: array, satu entry per produk yang disebutkan user
- line_index: index 0-based dari baris yang ingin diubah user (null = baris baru atau tidak diketahui)
- delivery_date: format YYYY-MM-DD, PER BARIS — setiap produk bisa punya tanggal berbeda

=== DELIVERY DATE PER BARIS ===
Jika user menyebut tanggal berbeda per produk:
"oksigen 5 tabung besok, nitrogen 3 tabung lusa"
→ order_lines[0].delivery_date = besok, order_lines[1].delivery_date = lusa

Jika user menyebut satu tanggal untuk semua:
"pesan oksigen 5 tabung dan nitrogen 3 tabung, kirim besok"
→ delivery_date = besok untuk SEMUA baris

Jika user tidak menyebut tanggal → delivery_date = null untuk baris tersebut

=== ENTITIES LEVEL ORDER ===
- customer_name: Nama individu (Jessica, Budi Santoso)
- customer_company: Nama organisasi (PT/CV/RS/Yayasan/Toko/UD/dll)
- cancellation_reason: Alasan cancel (hanya untuk CANCEL_ORDER)

=== ENTITIES LEVEL BARIS ===
- line_index: integer 0-based yang menunjukkan baris mana yang diupdate
- product_name: Nama produk
- quantity: Jumlah (integer)
- unit: Satuan quantity
- delivery_date: Tanggal pengiriman untuk produk ini (YYYY-MM-DD)

=== ATURAN PENENTUAN line_index (PENTING) ===
Tentukan line_index dengan urutan prioritas berikut:

1. User menyebut nomor item secara eksplisit
   "item 1" → line_index=0 | "item 2" → line_index=1 | "produk pertama" → line_index=0

2. User menyebut nama produk yang sudah ada di order
   Cocokkan dengan order_lines di CURRENT ORDER STATE → gunakan index baris yang cocok

3. User TIDAK menyebut item atau produk spesifik (jawaban vague: hanya tanggal, angka, satuan)
   → WAJIB gunakan ACTIVE_LINE_INDEX dari CURRENT ORDER STATE sebagai line_index
   Contoh: user reply "besok" saat ditanya tanggal → line_index = ACTIVE_LINE_INDEX

4. User menyebut produk BARU yang belum ada di order
   → line_index = null (sistem akan membuat baris baru)

JANGAN PERNAH mengembalikan line_index=null untuk jawaban vague (poin 3).
Jawaban vague SELALU ditujukan untuk ACTIVE_LINE_INDEX.

=== ATURAN PALING PENTING ===
**HANYA ekstrak dari USER MESSAGE terbaru.**
**JANGAN copy nilai dari CURRENT ORDER STATE.**
**CURRENT ORDER STATE hanya konteks, bukan sumber nilai.**

=== MODE EDIT ===
Jika EDIT_MODE: true, user mengubah baris yang sudah ada.
- "ubah tanggal item 1 jadi besok" → line_index=0, delivery_date=besok
- "ganti jumlah nitrogen jadi 10" → cari baris dengan product_name nitrogen, set quantity=10
- "ubah semua tanggal jadi rabu" → satu entry per baris yang ada, semua dengan delivery_date=rabu

=== KONVERSI TANGGAL ===
Gunakan CURRENT_DATE sebagai referensi:
- "besok" = CURRENT_DATE + 1 hari
- "lusa" = CURRENT_DATE + 2 hari
- "hari ini" = CURRENT_DATE
- Tanggal spesifik → konversi ke YYYY-MM-DD

=== ATURAN EKSTRAKSI PRODUK ===

**1. TABUNG/BOTOL (Gas kemasan)**
"3 tabung oksigen 6m3" → product_name="oksigen 6m3", quantity=3, unit="tabung"

**2. LIQUID/CURAH (Bulk)**
"liquid oxygen 10000 m3" → product_name="liquid oxygen", quantity=10000, unit="m3"

=== FORMAT OUTPUT ===
JSON only, tanpa markdown. Jika tidak ada produk yang disebutkan, order_lines = []."""


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

    active_line_index = current_order_state.get("active_line_index", 0)

    return f"""CURRENT_DATE: {current_date} ({current_day_id})
EDIT_MODE: {"true" if edit_mode else "false"}
ACTIVE_LINE_INDEX: {active_line_index}

CONVERSATION HISTORY (konteks saja, jangan copy ke entities):
{history_text}

CURRENT ORDER STATE (konteks saja — gunakan line_index untuk merujuk baris yang ada):
{current_order_state}

USER MESSAGE TERBARU (hanya ini yang harus diekstrak):
"{user_message}"

Ekstrak HANYA informasi yang ada dalam USER MESSAGE TERBARU. Set semua field yang tidak disebutkan ke null. order_lines boleh kosong [].
Ingat: jika jawaban user vague (hanya tanggal/angka/satuan tanpa menyebut produk atau item), gunakan ACTIVE_LINE_INDEX={active_line_index} sebagai line_index."""