# order_agent.py
# src/agents/order_agent.py
import json
from datetime import datetime

import pytz

from src.agents.base_agent import BaseAgent
from src.models.order_state import OrderState, OrderLine
from src.models.intent_result import ExtractedEntities
from src.services.llm_service import llm_service
from src.services.semantic_search_service import semantic_search_service
from src.core.conversation_manager import conversation_manager
from src.core.entity_extractor import entity_extractor


class OrderAgent(BaseAgent):

    # ------------------------------------------------------------------ #
    #  Entry point                                                         #
    # ------------------------------------------------------------------ #

    def handle(
        self,
        user_message: str,
        conversation_id: str,
        order_state: OrderState,
        context: list,
        **kwargs,
    ) -> str:
        intent: str = kwargs.get("intent", "ORDER")
        language: str = kwargs.get("language", "id")
        awaiting_confirmation: bool = kwargs.get("awaiting_confirmation", False)

        # ── 0. Confirmation ───────────────────────────────────────────────
        if awaiting_confirmation:
            response, new_flag = self._handle_confirmation_response(
                user_message, conversation_id, order_state, language
            )
            self._last_confirmation_flag = new_flag
            return response

        # ── 1. Completed order guard ──────────────────────────────────────
        if order_state.order_status == "completed" and intent == "ORDER":
            self._last_confirmation_flag = False
            return self._completed_order_response(order_state, user_message, context, language)

        # ── 2. Cancellation ───────────────────────────────────────────────
        if intent == "CANCEL_ORDER":
            return self._handle_cancellation(conversation_id, order_state, language)

        # ── 3. Extract entities ───────────────────────────────────────────
        entities = entity_extractor.extract(
            user_message=user_message,
            current_order_state=order_state,
            history=context[-4:],
        )

        # ── 4. Apply entities ─────────────────────────────────────────────
        if entities.has_any():
            date_error = self._apply_entities(entities, order_state)
            if date_error:
                return date_error

        # ── 5. Auto-fill customer ─────────────────────────────────────────
        self._maybe_autofill_customer(conversation_id, order_state)

        # ── 6. Persist ────────────────────────────────────────────────────
        conversation_manager.update_order_state(conversation_id, order_state)

        # ── 7. Check complete ─────────────────────────────────────────────
        order_state.update_missing_fields()
        if order_state.is_complete and order_state.order_status == "in_progress":
            self._last_confirmation_flag = True
            return self._generate_confirmation_prompt(order_state, language)

        # ── 8. Ask missing fields ─────────────────────────────────────────
        self._last_confirmation_flag = False
        return self._ask_for_missing_fields(user_message, order_state, context, language)

    # ------------------------------------------------------------------ #
    #  Cancellation                                                        #
    # ------------------------------------------------------------------ #

    def _handle_cancellation(
        self,
        conversation_id: str,
        order_state: OrderState,
        language: str,
    ) -> str:
        if order_state.order_status == "in_progress":
            conversation_manager.reset_order_state(conversation_id)
            return (
                "Order has been cancelled. Is there anything else I can help you with?"
                if language == "en"
                else "Pesanan telah dibatalkan. Ada yang bisa saya bantu lagi?"
            )

        previous_orders = conversation_manager.get_previous_orders(conversation_id)
        if previous_orders:
            return (
                "Sorry, for this service we will forward it to our call center. "
                "Please wait a moment, we will contact you back."
                if language == "en"
                else "Maaf, untuk layanan ini akan saya teruskan ke pihak call center kami. "
                "Mohon ditunggu sebentar, kami akan menghubungi Anda kembali."
            )

        return (
            "There is no active order to cancel. Is there anything I can help you with?"
            if language == "en"
            else "Tidak ada pesanan aktif yang bisa dibatalkan. Ada yang bisa saya bantu?"
        )

    # ------------------------------------------------------------------ #
    #  Entity application                                                  #
    # ------------------------------------------------------------------ #

    def _apply_entities(
        self,
        entities: ExtractedEntities,
        order_state: OrderState,
    ) -> str | None:
        current_line = order_state.order_lines[0] if order_state.order_lines else None

        # Product
        if entities.product_name:
            existing = current_line.product_name if current_line else None
            if entities.product_name != existing:
                self._resolve_product(entities, order_state)
            else:
                print(f"⏭️  Skipping semantic search — echo detected")

        # Customer
        if entities.customer_name and entities.customer_name != order_state.customer_name:
            order_state.customer_name = entities.customer_name

        if entities.customer_company and entities.customer_company != order_state.customer_company:
            order_state.customer_company = entities.customer_company

        # Date
        if entities.delivery_date and entities.delivery_date != order_state.delivery_date:
            error = self._validate_delivery_date(entities.delivery_date)
            if error:
                return error
            order_state.delivery_date = entities.delivery_date

        # Quantity/unit (no product mention)
        if not entities.product_name and order_state.order_lines:
            line = order_state.order_lines[0]
            if entities.quantity and entities.quantity != line.quantity:
                line.quantity = entities.quantity
            if entities.unit and entities.unit != line.unit:
                line.unit = entities.unit

        return None

    def _resolve_product(self, entities: ExtractedEntities, order_state: OrderState):
        matches = semantic_search_service.search_part_by_description(
            query=entities.product_name, top_k=3, threshold=0.55
        )
        if not matches:
            matches = semantic_search_service.fuzzy_search_by_description(
                query=entities.product_name, top_k=3
            )

        if not order_state.order_lines:
            order_state.order_lines.append(OrderLine())

        line = order_state.order_lines[0]

        if matches:
            best = matches[0]
            print(f"\n📋 Best match: {best['partnum']} | {best['description']} | score={best.get('similarity', 'fuzzy'):.4f}")
            line.partnum = best["partnum"]
            line.product_name = best["description"]
            line.unit = best.get("uom") or best.get("unit") or entities.unit
        else:
            line.product_name = entities.product_name
            line.unit = entities.unit

        if entities.quantity:
            line.quantity = entities.quantity

    # ------------------------------------------------------------------ #
    #  Auto-fill customer                                                  #
    # ------------------------------------------------------------------ #

    def _maybe_autofill_customer(self, conversation_id: str, order_state: OrderState):
        if order_state.customer_name and order_state.customer_company:
            return
        if order_state.order_status not in ("new", "in_progress"):
            return

        previous_orders = conversation_manager.get_previous_orders(conversation_id)
        if not previous_orders:
            return

        last = previous_orders[0]
        if not order_state.customer_name and last.get("customer_name"):
            order_state.customer_name = last["customer_name"]
            print("✅ Auto-filled customer_name from previous order")
        if not order_state.customer_company and last.get("customer_company"):
            order_state.customer_company = last["customer_company"]
            print("✅ Auto-filled customer_company from previous order")

    # ------------------------------------------------------------------ #
    #  Completed order response                                            #
    # ------------------------------------------------------------------ #

    def _completed_order_response(
        self, order_state: OrderState, user_message: str, context: list, language: str
    ) -> str:
        order_json = json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)

        if language == "en":
            system_prompt = f"""You are a professional customer service representative.

IMPORTANT — ORDER ALREADY COMPLETED:
The customer's order is COMPLETED and cannot be modified.
- Answer questions about the previous order politely.
- If they want to modify/cancel: direct them to customer service.
- If they want to order again: offer to create a NEW order.

PREVIOUS ORDER (COMPLETED):
{order_json}

Maximum 2-3 sentences per response."""
        else:
            system_prompt = f"""Anda adalah customer service profesional.

PENTING — PESANAN SUDAH SELESAI:
Pesanan customer ini sudah COMPLETED dan tidak bisa diubah.
- Jawab pertanyaan tentang pesanan sebelumnya dengan ramah.
- Jika ingin ubah/cancel: arahkan ke customer service.
- Jika ingin pesan lagi: tawarkan untuk membuat pesanan BARU.

PESANAN SEBELUMNYA (COMPLETED):
{order_json}

Maksimal 2-3 kalimat per respons."""

        return llm_service.chat(
            user_message=user_message,
            system_prompt=system_prompt,
            conversation_history=context[:-1],
        )

    # ------------------------------------------------------------------ #
    #  Ask for missing fields                                              #
    # ------------------------------------------------------------------ #

    def _ask_for_missing_fields(
        self,
        user_message: str,
        order_state: OrderState,
        context: list,
        language: str,
    ) -> str:
        order_json = json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)

        if language == "en":
            system_prompt = f"""You are a professional call center customer service representative in Indonesia helping customers order industrial products.

SPEAKING STYLE:
- Natural, friendly, professional English
- Use "you" or "Sir/Madam"
- Vary responses, never monotonous

YOUR TASK:
- Ask for missing order information naturally
- Ensure you collect: product, quantity, unit, delivery date, customer name, company/organization

CURRENT ORDER STATE:
{order_json}

RULES:
- Answer any customer question first, then continue
- Ask for one missing field at a time
- Maximum 2-3 sentences per response"""
        else:
            system_prompt = f"""Anda adalah customer service call center profesional di Indonesia yang membantu pelanggan memesan produk industrial.

GAYA BICARA:
- Bahasa Indonesia natural dan ramah
- Gunakan "Anda" atau "Bapak/Ibu"
- Variasikan respons

TUGAS:
- Tanyakan informasi pesanan yang masih kurang secara natural
- Pastikan mendapatkan: produk, jumlah, satuan, tanggal kirim, nama customer, nama perusahaan/organisasi

INFORMASI PESANAN SAAT INI:
{order_json}

ATURAN:
- Jawab pertanyaan customer dulu sebelum melanjutkan
- Tanyakan satu informasi yang kosong per respons
- Maksimal 2-3 kalimat per respons"""

        return llm_service.chat(
            user_message=user_message,
            system_prompt=system_prompt,
            conversation_history=context[:-1],
        )

    # ------------------------------------------------------------------ #
    #  Confirmation prompt                                                 #
    # ------------------------------------------------------------------ #

    def _generate_confirmation_prompt(self, order_state: OrderState, language: str) -> str:
        line = order_state.order_lines[0]
        product_info = (
            f"{line.product_name} ({line.partnum})"
            if line.partnum
            else line.product_name
        )

        if language == "en":
            return f"""Alright, let me confirm your order:

📦 ORDER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Product     : {product_info}
Quantity    : {line.quantity} {line.unit}
Name        : {order_state.customer_name}
Company     : {order_state.customer_company}
Date        : {order_state.delivery_date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Is the information correct to process?

Type:
- "Yes" / "Correct" to confirm
- "Change [field]" to modify (e.g., "Change date")
- "Cancel" to cancel"""

        return f"""Baik, saya konfirmasi pesanan Bapak/Ibu:

📦 DETAIL PESANAN:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Produk      : {product_info}
Jumlah      : {line.quantity} {line.unit}
Nama        : {order_state.customer_name}
Perusahaan  : {order_state.customer_company}
Tanggal     : {order_state.delivery_date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apakah data sudah benar untuk diproses?

Ketik:
- "Ya" / "Benar" untuk konfirmasi pesanan
- "Ubah [field]" untuk mengubah (contoh: "Ubah tanggal")
- "Batal" untuk membatalkan pesanan"""

    # ------------------------------------------------------------------ #
    #  Confirmation response handling                                      #
    # ------------------------------------------------------------------ #

    def _handle_confirmation_response(self, user_message, conversation_id, order_state, language):
        user_input = user_message.lower().strip()

        # ── Edit DULU sebelum confirm ────────────────────────────────────
        # Harus di atas confirm check karena "ubah ... ya" mengandung confirm word
        edit_words = ["ubah", "edit", "ganti", "salah", "change", "modify"]
        if any(w in user_input for w in edit_words):
            return self._handle_edit_request(user_message, conversation_id, order_state, language)

        # ── Cancel ───────────────────────────────────────────────────────
        cancel_words = ["batal", "cancel", "stop", "gak jadi", "tidak jadi"]
        if any(w in user_input for w in cancel_words):
            conversation_manager.reset_order_state(conversation_id)
            msg = (
                "Order cancelled. Is there anything else I can help you with?"
                if language == "en"
                else "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"
            )
            return msg, False

        # ── Confirm ──────────────────────────────────────────────────────
        confirm_words = ["ya", "konfirmasi", "yes", "ok", "oke", "benar", "betul"]
        is_confirm = any(
            user_input == w
            or user_input.startswith(w + " ")
            or user_input.endswith(" " + w)
            for w in confirm_words
        )
        if is_confirm:
            return self._complete_order(conversation_id, order_state, language), False

        # ── Unclear ──────────────────────────────────────────────────────
        msg = (
            "Sorry, I didn't quite understand.\n\n"
            "Is the order correct?\n"
            '- Type "Yes" to confirm\n'
            '- Type "Change [field] to [value]" to modify\n'
            '- Type "Cancel" to cancel'
            if language == "en"
            else
            "Maaf, saya kurang mengerti.\n\n"
            "Apakah data pesanan sudah benar?\n"
            '- Ketik "Ya" untuk konfirmasi\n'
            '- Ketik "Ubah [field] jadi [value]" untuk mengubah\n'
            '- Ketik "Batal" untuk membatalkan'
        )
        return msg, True

    def _handle_edit_request(
        self,
        user_message: str,
        conversation_id: str,
        order_state: OrderState,
        language: str,
    ) -> tuple[str, bool]:
        """Use entity_extractor with edit_mode=True to extract changes."""
        entities = entity_extractor.extract(
            user_message=user_message,
            current_order_state=order_state,
            history=[],
            edit_mode=True,
        )

        if not entities.has_any():
            msg = (
                "Alright, which field would you like to change? "
                "(e.g., 'change date to tomorrow', 'change company to CV ABC')"
                if language == "en"
                else "Baik, field apa yang ingin diubah? "
                "(contoh: 'ubah tanggal jadi besok', 'ganti perusahaan jadi CV ABC')"
            )
            return msg, True

        changes = entities.model_dump(exclude_none=True)
        result = self._apply_order_changes(order_state, changes)

        if isinstance(result, dict) and "error" in result:
            return result["error"], True

        if result:
            order_state.update_missing_fields()
            conversation_manager.update_order_state(conversation_id, order_state)
            return self._generate_confirmation_prompt(order_state, language), True

        msg = (
            "Sorry, I couldn't understand the changes. Could you explain in more detail?"
            if language == "en"
            else "Maaf, saya tidak bisa memahami perubahan yang Anda inginkan. Bisa dijelaskan lebih detail?"
        )
        return msg, True

    def _apply_order_changes(self, order_state: OrderState, changes: dict):
        """Apply changes dict to order_state in-place. Returns True if applied, error dict if invalid."""
        applied = False

        if changes.get("customer_name"):
            order_state.customer_name = changes["customer_name"]
            applied = True

        if changes.get("customer_company"):
            order_state.customer_company = changes["customer_company"]
            applied = True

        if changes.get("delivery_date"):
            error = self._validate_delivery_date(changes["delivery_date"])
            if error:
                return {"error": error}
            order_state.delivery_date = changes["delivery_date"]
            applied = True

        if order_state.order_lines:
            if changes.get("product_name"):
                matches = semantic_search_service.search_part_by_description(
                    query=changes["product_name"], top_k=3, threshold=0.55
                )
                if matches:
                    best = matches[0]
                    order_state.order_lines[0].product_name = best["description"]
                    order_state.order_lines[0].partnum = best["partnum"]
                    applied = True

            if changes.get("quantity"):
                order_state.order_lines[0].quantity = changes["quantity"]
                applied = True

            if changes.get("unit"):
                order_state.order_lines[0].unit = changes["unit"]
                applied = True

        return applied

    # ------------------------------------------------------------------ #
    #  Complete order                                                      #
    # ------------------------------------------------------------------ #

    def _complete_order(
        self, conversation_id: str, order_state: OrderState, language: str
    ) -> str:
        order_id = self._save_order_to_database(conversation_id, order_state)
        conversation_manager.mark_order_completed(conversation_id)
        conversation_manager.reset_order_state(conversation_id)

        line = order_state.order_lines[0]

        if language == "en":
            return f"""✅ ORDER SUCCESSFULLY CONFIRMED!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Order Number: {order_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Product     : {line.product_name}
Quantity    : {line.quantity} {line.unit}
Date        : {order_state.delivery_date}
Customer    : {order_state.customer_name}
Company     : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Thank you! Your order is being processed.
You will receive updates via WhatsApp.

Is there anything else I can help you with?"""

        return f"""✅ PESANAN BERHASIL DIKONFIRMASI!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Nomor Pesanan: {order_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Produk      : {line.product_name}
Jumlah      : {line.quantity} {line.unit}
Tanggal     : {order_state.delivery_date}
Customer    : {order_state.customer_name}
Perusahaan  : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Terima kasih! Pesanan Anda sedang diproses.
Anda akan menerima update melalui WhatsApp.

Ada yang bisa saya bantu lagi?"""

    def _save_order_to_database(self, conversation_id: str, order_state: OrderState) -> str:
        from src.database.sql_schema import Order
        from src.services.sql_service import SQLService

        svc = SQLService()
        try:
            now = datetime.now()
            date_str = now.strftime("%Y%m%d")
            today_count = svc.db.query(Order).filter(
                Order.order_id.like(f"ORD-{date_str}-%")
            ).count()
            order_id = f"ORD-{date_str}-{today_count + 1:04d}"

            items = [
                {
                    "partnum": line.partnum,
                    "product_name": line.product_name,
                    "quantity": line.quantity,
                    "unit": line.unit,
                }
                for line in order_state.order_lines
            ]

            phone_number = conversation_manager.get_phone_number(conversation_id)

            new_order = Order(
                order_id=order_id,
                conversation_id=conversation_id,
                customer_name=order_state.customer_name,
                customer_company=order_state.customer_company,
                customer_phone=phone_number,
                delivery_date=order_state.delivery_date,
                status="confirmed",
                items=items,
                created_at=now,
                updated_at=now,
            )
            svc.db.add(new_order)
            svc.db.commit()
            print(f"✅ Order saved: {order_id}")
            return order_id

        except Exception as e:
            print(f"❌ Error saving order: {e}")
            svc.db.rollback()
            return f"ORD-{datetime.now().strftime('%Y%m%d')}-TEMP"
        finally:
            svc.close()

    # ------------------------------------------------------------------ #
    #  Resume flow                                                         #
    # ------------------------------------------------------------------ #

    def generate_resume_prompt(self, last_order_state: dict) -> str:
        customer_name = last_order_state.get("customer_name", "")
        order_lines = last_order_state.get("order_lines", [])

        order_summary = ""
        if order_lines:
            line = order_lines[0]
            product = line.get("product_name", "")
            quantity = line.get("quantity", "")
            unit = line.get("unit", "")
            if product:
                order_summary = f"\n- Produk: {product}"
                if quantity:
                    order_summary += f"\n- Jumlah: {quantity} {unit}".rstrip()

        greeting = f"Halo {customer_name}!" if customer_name else "Halo!"

        if order_summary:
            return (
                f"{greeting} Sepertinya pesanan Anda sebelumnya:{order_summary}\n\n"
                "belum selesai. Apakah ingin melanjutkan pesanan ini?\n\n"
                'Ketik:\n- "Ya" / "Lanjut" untuk melanjutkan\n- "Mulai Baru" untuk membuat pesanan baru'
            )

        return (
            f"{greeting} Sepertinya Anda memiliki pesanan yang belum selesai.\n\n"
            "Apakah ingin melanjutkan pesanan sebelumnya?\n\n"
            'Ketik:\n- "Ya" / "Lanjut" untuk melanjutkan\n- "Mulai Baru" untuk membuat pesanan baru'
        )

    def handle_resume_response(
        self, user_message: str, conversation_id: str, language: str
    ) -> str:
        user_input = user_message.lower().strip()
        continue_words = ["ya", "lanjut", "iya", "yes", "continue", "ok", "oke"]
        restart_words = ["baru", "mulai baru", "gak", "tidak", "no", "cancel"]

        if any(w in user_input for w in continue_words):
            order_state = conversation_manager.get_order_state(conversation_id)
            context = conversation_manager.get_context(conversation_id)
            return self._ask_for_missing_fields(
                "lanjutkan pesanan", order_state, context, language
            )

        if any(w in user_input for w in restart_words):
            fresh = OrderState()
            fresh.order_status = "new"
            conversation_manager.update_order_state(conversation_id, fresh)
            return "Baik, kita mulai pesanan baru. Produk apa yang ingin Anda pesan?"

        return (
            "Maaf, saya kurang mengerti.\n\n"
            'Apakah Anda ingin melanjutkan pesanan sebelumnya?\n'
            'Ketik "Ya" untuk melanjutkan atau "Mulai Baru" untuk pesanan baru.'
        )

    # ------------------------------------------------------------------ #
    #  Date validation                                                     #
    # ------------------------------------------------------------------ #

    def _validate_delivery_date(self, delivery_date: str) -> str | None:
        wib = pytz.timezone("Asia/Jakarta")
        today = datetime.now(wib).date()

        try:
            delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
        except ValueError:
            return (
                "Maaf, format tanggal tidak valid. "
                "Mohon berikan tanggal yang jelas (contoh: 'besok', '15 Februari')."
            )

        delivery_date_obj = delivery_dt.date()

        if delivery_date_obj < today:
            days_ago = (today - delivery_date_obj).days
            time_desc = (
                "kemarin" if days_ago == 1
                else "kemarin lusa" if days_ago == 2
                else f"{days_ago} hari yang lalu"
            )
            return (
                f"Maaf, tanggal {delivery_date} itu sudah lewat ({time_desc}). "
                "Untuk tanggal berapa ya pengirimannya?"
            )

        if delivery_dt.weekday() == 6:
            month_map = {
                "January": "Januari", "February": "Februari", "March": "Maret",
                "April": "April", "May": "Mei", "June": "Juni", "July": "Juli",
                "August": "Agustus", "September": "September", "October": "Oktober",
                "November": "November", "December": "Desember",
            }
            date_formatted = delivery_dt.strftime("%d %B %Y")
            for eng, ind in month_map.items():
                date_formatted = date_formatted.replace(eng, ind)
            return (
                f"Maaf, tanggal {date_formatted} itu hari Minggu. "
                "Kami tidak melayani pengiriman di hari Minggu. Bisa pilih tanggal lain?"
            )

        return None


# Singleton
order_agent = OrderAgent()