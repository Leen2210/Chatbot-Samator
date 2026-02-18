# order_agent.py
# src/agents/order_agent.py
"""
OrderAgent owns the complete order lifecycle:
  - Field collection (ask for missing data)
  - Semantic product search
  - Date validation
  - Confirmation prompt & response handling
  - Cancellation
  - Saving to DB
  - Resume flow
"""
import json
from datetime import datetime

import pytz

from src.agents.base_agent import BaseAgent
from src.models.order_state import OrderState, OrderLine
from src.models.intent_result import IntentResult
from src.services.llm_service import llm_service
from src.services.semantic_search_service import semantic_search_service
from src.core.conversation_manager import conversation_manager


class OrderAgent(BaseAgent):
    """
    Handles ORDER and CANCEL_ORDER intents end-to-end.
    The Orchestrator calls this agent; the agent returns a string response.
    All DB writes go through ConversationManager or _save_order_to_database().
    """

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
        """
        Main entry point called by the Orchestrator.

        kwargs expected:
            intent_result (IntentResult)
            language (str)               – 'id' or 'en'
            awaiting_confirmation (bool) – flag from orchestrator state
        """
        intent_result: IntentResult | None = kwargs.get("intent_result")
        language: str = kwargs.get("language", "id")
        awaiting_confirmation: bool = kwargs.get("awaiting_confirmation", False)

        # ── 0. Confirmation response (highest priority) ───────────────── #
        # The Orchestrator only reaches here with awaiting_confirmation=True
        # when it has already skipped intent classification. "ya", "yes",
        # "batal", "ubah tanggal" etc. all land here correctly regardless of
        # what the LLM would have classified them as.
        if awaiting_confirmation:
            response, new_flag = self._handle_confirmation_response(
                user_message, conversation_id, order_state, language
            )
            self._last_confirmation_flag = new_flag
            return response

        # Below this point intent_result is always present (normal ORDER/CANCEL flow)

        # ── 1. Completed order guard ──────────────────────────────────── #
        if order_state.order_status == "completed" and intent_result.intent == "ORDER":
            self._last_confirmation_flag = False
            return self._completed_order_response(order_state, user_message, context, language)

        # ── 2. Cancellation ──────────────────────────────────────────── #
        if intent_result.intent == "CANCEL_ORDER":
            return self._handle_cancellation(
                conversation_id, order_state, language
            )

        # ── (old step 2 removed — confirmation is now step 0 above) ──── #
        # ── 3. Apply extracted entities to order state ────────────────── #
        if intent_result.has_entities():
            date_error = self._apply_entities(
                intent_result, conversation_id, order_state
            )
            if date_error:
                return date_error  # Return validation error immediately

        # ── 4. Auto-fill customer data from previous orders ───────────── #
        self._maybe_autofill_customer(conversation_id, order_state)

        # ── 5. Persist updated state ──────────────────────────────────── #
        conversation_manager.update_order_state(conversation_id, order_state)

        # ── 6. Check if order is now complete → confirmation prompt ───── #
        order_state.update_missing_fields()
        if order_state.is_complete and order_state.order_status == "in_progress":
            self._last_confirmation_flag = True
            return self._generate_confirmation_prompt(order_state, language)

        # ── 7. Ask for next missing field ─────────────────────────────── #
        self._last_confirmation_flag = False
        return self._ask_for_missing_fields(
            user_message, order_state, context, language
        )

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
        intent_result: IntentResult,
        conversation_id: str,
        order_state: OrderState,
    ) -> str | None:
        """
        Apply extracted entities to the order state in-place.
        Returns a validation error string if a date is invalid, else None.
        """
        e = intent_result.entities

        # Product via semantic search
        if e.product_name:
            self._resolve_product(e, order_state)

        # Scalar fields
        if e.customer_name:
            order_state.customer_name = e.customer_name
        if e.customer_company:
            order_state.customer_company = e.customer_company
        if e.delivery_date:
            error = self._validate_delivery_date(e.delivery_date)
            if error:
                return error
            order_state.delivery_date = e.delivery_date

        # Quantity/unit without product
        if not e.product_name and order_state.order_lines:
            line = order_state.order_lines[0]
            if e.quantity:
                line.quantity = e.quantity
            if e.unit:
                line.unit = e.unit

        return None

    def _resolve_product(self, entities, order_state: OrderState):
        """Run semantic search and update the first order line."""
        matches = semantic_search_service.search_part_by_description(
            query=entities.product_name, top_k=3, threshold=0.55
        )

        if not matches:
            matches = semantic_search_service.fuzzy_search_by_description(
                query=entities.product_name, top_k=3
            )

        if len(order_state.order_lines) == 0:
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
    #  Auto-fill customer from previous orders                             #
    # ------------------------------------------------------------------ #

    def _maybe_autofill_customer(self, conversation_id: str, order_state: OrderState):
        if order_state.customer_name and order_state.customer_company:
            return  # Already have customer data
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
    #  Completed order response (LLM)                                      #
    # ------------------------------------------------------------------ #

    def _completed_order_response(
        self, order_state: OrderState, user_message: str, context: list, language: str
    ) -> str:
        """
        Called when the user sends an ORDER message but the order is already confirmed.
        Tells them it's locked, offers to start a new one.
        Belongs here in OrderAgent — it's an order-domain concern.
        """
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
    #  Ask for missing fields (LLM)                                        #
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
- If all fields are complete, show confirmation summary
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
- Jika semua lengkap, tampilkan konfirmasi
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

    def _handle_confirmation_response(
        self,
        user_message: str,
        conversation_id: str,
        order_state: OrderState,
        language: str,
    ) -> tuple[str, bool]:
        """
        Returns (response_string, new_awaiting_confirmation_flag).
        """
        user_input = user_message.lower().strip()

        # ── Confirm ─────────────────────────────────────────────────────
        confirm_words = ["ya", "konfirmasi", "yes", "ok", "oke", "benar", "betul"]
        is_confirm = any(
            user_input == w
            or user_input.startswith(w + " ")
            or user_input.endswith(" " + w)
            for w in confirm_words
        )
        if is_confirm:
            response = self._complete_order(conversation_id, order_state, language)
            return response, False  # confirmation done

        # ── Cancel ──────────────────────────────────────────────────────
        cancel_words = ["batal", "cancel", "stop", "gak jadi", "tidak jadi"]
        if any(w in user_input for w in cancel_words):
            conversation_manager.reset_order_state(conversation_id)
            msg = (
                "Order cancelled. Is there anything else I can help you with?"
                if language == "en"
                else "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"
            )
            return msg, False

        # ── Edit ────────────────────────────────────────────────────────
        edit_words = ["ubah", "edit", "ganti", "salah", "change", "modify"]
        if any(w in user_input for w in edit_words):
            return self._handle_edit_request(
                user_message, conversation_id, order_state, language
            )

        # ── Unclear ──────────────────────────────────────────────────────
        if language == "en":
            msg = (
                "Sorry, I didn't quite understand.\n\n"
                "Is the order correct?\n"
                '- Type "Yes" to confirm\n'
                '- Type "Change [field] to [value]" to modify\n'
                '- Type "Cancel" to cancel'
            )
        else:
            msg = (
                "Maaf, saya kurang mengerti.\n\n"
                "Apakah data pesanan sudah benar?\n"
                '- Ketik "Ya" untuk konfirmasi\n'
                '- Ketik "Ubah [field] jadi [value]" untuk mengubah\n'
                '- Ketik "Batal" untuk membatalkan'
            )
        return msg, True  # still awaiting

    def _handle_edit_request(
        self,
        user_message: str,
        conversation_id: str,
        order_state: OrderState,
        language: str,
    ) -> tuple[str, bool]:
        """Use LLM to extract the desired changes and apply them."""
        changes_result = self._extract_order_changes(user_message, order_state)

        if not changes_result.get("has_changes"):
            msg = (
                "Alright, which field would you like to change? "
                "(e.g., 'change date to tomorrow', 'change company to CV ABC')"
                if language == "en"
                else "Baik, field apa yang ingin diubah? "
                "(contoh: 'ubah tanggal jadi besok', 'ganti perusahaan jadi CV ABC')"
            )
            return msg, True

        result = self._apply_order_changes(order_state, changes_result["changes"])
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

    def _extract_order_changes(self, user_message: str, order_state: OrderState) -> dict:
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        day_map = {
            "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
            "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu",
        }
        current_day_id = day_map.get(now.strftime("%A"), now.strftime("%A"))

        system_prompt = f"""Anda adalah sistem ekstraksi perubahan pesanan.

CURRENT_DATE: {current_date} ({current_day_id})

CURRENT ORDER STATE:
{json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)}

USER MESSAGE: "{user_message}"

OUTPUT FORMAT (JSON only, no markdown):
{{
  "has_changes": true/false,
  "changes": {{
    "customer_name": "nilai baru atau null",
    "customer_company": "nilai baru atau null",
    "delivery_date": "YYYY-MM-DD atau null",
    "product_name": "nilai baru atau null",
    "quantity": angka_atau_null,
    "unit": "nilai baru atau null"
  }}
}}

RULES:
- "besok" = {current_date} + 1 day
- "lusa" = {current_date} + 2 days
- Only set fields that ARE changing; leave others null
- has_changes: false if no clear change detected"""

        try:
            response = llm_service.chat(
                user_message=user_message,
                system_prompt=system_prompt,
                conversation_history=[],
            )
            import re
            cleaned = re.sub(r"^```json\s*", "", response.strip())
            cleaned = re.sub(r"^```\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            return json.loads(cleaned)
        except Exception as e:
            print(f"⚠️ Error extracting changes: {e}")
            return {"has_changes": False, "changes": {}}

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
    #  Resume flow (called by Orchestrator)                                #
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
    #  Date validation (pure business rule, no LLM)                       #
    # ------------------------------------------------------------------ #

    def _validate_delivery_date(self, delivery_date: str) -> str | None:
        """Return error string if date is invalid, else None."""
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

        if delivery_dt.weekday() == 6:  # Sunday
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