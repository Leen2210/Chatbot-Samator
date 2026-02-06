# src/config/prompts/dialog_prompts.py

WELCOME_TEMPLATE = """Halo! Selamat datang di layanan pemesanan kami. 👋

Silakan pilih layanan yang Anda butuhkan:

1️⃣ Order - Pesan produk/parts
2️⃣ Other - Pertanyaan umum atau bantuan lainnya

Ketik angka (1 atau 2) atau langsung tulis kebutuhan Anda.
Contoh: "Mau pesan oksigen 5 tabung" """

ORDER_GREETING = """Terima kasih! Saya siap membantu pesanan Anda. 

Produk apa yang ingin Anda pesan?"""

CANCEL_CONFIRMATION = """Baik, pesanan Anda dibatalkan.

Apakah ada yang bisa saya bantu lagi?"""

FALLBACK_REDIRECT = """Maaf, untuk pertanyaan ini saya hubungkan ke customer service kami ya.

Tim kami akan segera menghubungi Anda. Terima kasih!"""

INVALID_SELECTION = """Maaf, saya tidak mengerti pilihan Anda.

Silakan ketik:
1️⃣ untuk Order
2️⃣ untuk Other/Bantuan

Atau langsung tulis kebutuhan Anda."""