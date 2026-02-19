# order_agent.py
# src/agents/order_agent.py
import json
from datetime import datetime

import pytz

from src.agents.base_agent import BaseAgent
from src.models.order_state import OrderState, OrderLine
from src.models.intent_result import ExtractedEntities, ExtractedOrderLine
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

        if awaiting_confirmation:
            response, new_flag = self._handle_confirmation_response(
                user_message, conversation_id, order_state, language
            )
            self._last_confirmation_flag = new_flag
            return response

        if order_state.order_status == "completed" and intent == "ORDER":
            self._last_confirmation_flag = False
            return self._completed_order_response(order_state, user_message, context, language)

        if intent == "CANCEL_ORDER":
            return self._handle_cancellation(conversation_id, order_state, language)

        entities = entity_extractor.extract(
            user_message=user_message,
            current_order_state=order_state,
            history=context[-4:],
        )

        if entities.has_any():
            date_error = self._apply_entities(entities, order_state)
            if date_error:
                return date_error

        self._maybe_autofill_customer(conversation_id, order_state)
        conversation_manager.update_order_state(conversation_id, order_state)

        order_state.update_missing_fields()
        if order_state.is_complete and order_state.order_status == "in_progress":
            self._last_confirmation_flag = True
            return self._generate_confirmation_prompt(order_state, language)

        self._last_confirmation_flag = False
        return self._ask_for_missing_fields(user_message, order_state, context, language)

    # ------------------------------------------------------------------ #
    #  Cancellation                                                        #
    # ------------------------------------------------------------------ #

    def _handle_cancellation(self, conversation_id, order_state, language):
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
                "Sorry, for this service we will forward it to our call center."
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
    #  Entity application — multi-line aware                               #
    # ------------------------------------------------------------------ #

    def _apply_entities(self, entities: ExtractedEntities, order_state: OrderState) -> str | None:
        # ── Order-level fields ──────────────────────────────────────────
        if entities.customer_name and entities.customer_name != order_state.customer_name:
            order_state.customer_name = entities.customer_name

        if entities.customer_company and entities.customer_company != order_state.customer_company:
            order_state.customer_company = entities.customer_company

        # ── Per-line fields ─────────────────────────────────────────────
        for extracted_line in entities.order_lines:
            if not extracted_line.has_any():
                continue

            # Resolve product FIRST (so we have a partnum before duplicate check)
            resolved_partnum = None
            resolved_description = None
            resolved_unit = None
            if extracted_line.product_name:
                resolved_partnum, resolved_description, resolved_unit = \
                    self._lookup_product(extracted_line.product_name)

            target_line = self._resolve_target_line(
                extracted_line, order_state,
                resolved_partnum=resolved_partnum,
                resolved_description=resolved_description,
            )

            # Apply product fields
            if extracted_line.product_name:
                if resolved_partnum:
                    target_line.partnum = resolved_partnum
                    target_line.product_name = resolved_description
                    target_line.unit = target_line.unit or resolved_unit or extracted_line.unit
                else:
                    target_line.product_name = extracted_line.product_name
                    target_line.unit = target_line.unit or extracted_line.unit

                if extracted_line.quantity:
                    target_line.quantity = extracted_line.quantity
                if extracted_line.unit:
                    target_line.unit = extracted_line.unit

            else:
                # No product mention — just update qty/unit if provided
                if extracted_line.quantity and extracted_line.quantity != target_line.quantity:
                    target_line.quantity = extracted_line.quantity
                if extracted_line.unit and extracted_line.unit != target_line.unit:
                    target_line.unit = extracted_line.unit

            # Per-line delivery date
            if extracted_line.delivery_date and extracted_line.delivery_date != target_line.delivery_date:
                error = self._validate_delivery_date(extracted_line.delivery_date)
                if error:
                    return error
                target_line.delivery_date = extracted_line.delivery_date

        return None

    def _lookup_product(self, product_name: str) -> tuple[str | None, str | None, str | None]:
        """
        Run semantic + fuzzy search for a product name.
        Returns (partnum, description, uom) or (None, None, None) if no match.
        """
        matches = semantic_search_service.search_part_by_description(
            query=product_name, top_k=3, threshold=0.55
        )
        if not matches:
            matches = semantic_search_service.fuzzy_search_by_description(
                query=product_name, top_k=3
            )
        if matches:
            best = matches[0]
            print(f"\n📋 Best match: {best['partnum']} | {best['description']} | score={best.get('similarity', 'fuzzy')}")
            return best["partnum"], best["description"], best.get("uom") or best.get("unit")
        return None, None, None

    def _resolve_target_line(
        self,
        extracted: ExtractedOrderLine,
        order_state: OrderState,
        resolved_partnum: str | None = None,
        resolved_description: str | None = None,
    ) -> OrderLine:
        """
        Find the correct OrderLine to write into, following this priority:

        1. Explicit line_index from user ("item 1", "produk pertama")
        2. Duplicate guard — partnum match (same product already in order)
        3. Duplicate guard — product name fuzzy match
        4. active_line_index fallback (for vague follow-up answers like "besok")
        5. First empty line reuse
        6. Append new line (only when a new product is genuinely being added)
        """

        # 1. Explicit index
        if extracted.line_index is not None:
            if extracted.line_index < len(order_state.order_lines):
                return order_state.order_lines[extracted.line_index]

        # 2. Duplicate guard — partnum (strongest signal)
        if resolved_partnum:
            for line in order_state.order_lines:
                if line.partnum and line.partnum == resolved_partnum:
                    print(f"♻️  Duplicate detected by partnum ({resolved_partnum}) → updating existing line")
                    return line

        # 3. Duplicate guard — product name fuzzy match
        if resolved_description or extracted.product_name:
            lookup_name = (resolved_description or extracted.product_name).lower()
            for line in order_state.order_lines:
                if line.product_name and lookup_name in line.product_name.lower():
                    print(f"♻️  Duplicate detected by name match → updating existing line")
                    return line

        # 4. No product mentioned — vague follow-up ("besok", "5", "tabung")
        #    Route to active_line_index so we fill the right line
        if not extracted.product_name:
            idx = order_state.active_line_index
            if idx < len(order_state.order_lines):
                return order_state.order_lines[idx]

        # 5. First line is empty — reuse before creating new
        if order_state.order_lines and not order_state.order_lines[0].product_name:
            return order_state.order_lines[0]

        # 6. Genuinely new product — append
        new_line = OrderLine()
        order_state.order_lines.append(new_line)
        return new_line

    # ------------------------------------------------------------------ #
    #  Auto-fill customer                                                  #
    # ------------------------------------------------------------------ #

    def _maybe_autofill_customer(self, conversation_id, order_state):
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
        if not order_state.customer_company and last.get("customer_company"):
            order_state.customer_company = last["customer_company"]

    # ------------------------------------------------------------------ #
    #  Completed order response                                            #
    # ------------------------------------------------------------------ #

    def _completed_order_response(self, order_state, user_message, context, language):
        order_json = json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)
        if language == "en":
            system_prompt = f"""You are a professional customer service representative.
The customer's order is COMPLETED and cannot be modified.
Answer questions about it, direct modifications to customer service, offer a NEW order if they want to re-order.
PREVIOUS ORDER: {order_json}
Max 2-3 sentences."""
        else:
            system_prompt = f"""Anda adalah customer service profesional.
Pesanan ini sudah COMPLETED dan tidak bisa diubah.
Jawab pertanyaan tentang pesanan sebelumnya, arahkan ubah/cancel ke customer service, tawarkan pesanan BARU jika perlu.
PESANAN SEBELUMNYA: {order_json}
Maksimal 2-3 kalimat."""
        return llm_service.chat(user_message=user_message, system_prompt=system_prompt, conversation_history=context[:-1])

    # ------------------------------------------------------------------ #
    #  Ask for missing fields — structured missing_fields + active line    #
    # ------------------------------------------------------------------ #

    def _ask_for_missing_fields(self, user_message, order_state, context, language):
        order_json = json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)

        # Build a human-readable summary of what's still missing so the LLM
        # doesn't have to parse the structured dict itself
        missing_summary = _format_missing_for_prompt(order_state.missing_fields, order_state.order_lines)
        active_idx = order_state.active_line_index
        active_product = None
        if active_idx < len(order_state.order_lines):
            active_product = order_state.order_lines[active_idx].product_name or f"item {active_idx + 1}"

        if language == "en":
            system_prompt = f"""You are a professional call center customer service representative helping customers order industrial products.

SPEAKING STYLE: Natural, friendly, professional English. Use "you" or "Sir/Madam".

YOUR TASK:
- Collect all required information for each product: product name, quantity, unit, and delivery date.
- Customer name and company are also required once.
- Each product line can have a DIFFERENT delivery date.
- After ALL lines are complete, ask "Would you like to add another product?" before confirming.
- Ask for ONE missing field at a time.

CURRENTLY COLLECTING FOR: {active_product} (line index {active_idx})

WHAT IS STILL MISSING:
{missing_summary}

FULL ORDER STATE:
{order_json}

RULES:
- Your next question must be about the field for line index {active_idx} listed first in the missing list above.
- If the user's answer is vague (e.g., just a date or number), assume it's for line index {active_idx}.
- Answer any customer question first, then continue.
- Maximum 2-3 sentences per response."""

        else:
            system_prompt = f"""Anda adalah customer service call center profesional yang membantu pelanggan memesan produk industrial.

GAYA BICARA: Bahasa Indonesia natural dan ramah. Gunakan "Anda" atau "Bapak/Ibu".

TUGAS:
- Kumpulkan semua info untuk setiap produk: nama produk, jumlah, satuan, dan tanggal kirim.
- Nama customer dan perusahaan juga diperlukan satu kali.
- Setiap baris produk BISA punya tanggal kirim yang BERBEDA.
- Setelah SEMUA baris lengkap, tanya "Ada produk lain yang ingin dipesan?" sebelum konfirmasi.
- Tanyakan SATU informasi yang kosong per respons.

SEDANG MENGUMPULKAN UNTUK: {active_product} (line index {active_idx})

YANG MASIH KURANG:
{missing_summary}

STATUS PESANAN LENGKAP:
{order_json}

ATURAN:
- Pertanyaan berikutnya HARUS tentang field untuk line index {active_idx} yang tercantum pertama di daftar di atas.
- Jika jawaban user tidak spesifik (misal hanya tanggal atau angka), asumsikan itu untuk line index {active_idx}.
- Jawab pertanyaan customer dulu sebelum melanjutkan.
- Maksimal 2-3 kalimat per respons."""

        return llm_service.chat(
            user_message=user_message,
            system_prompt=system_prompt,
            conversation_history=context[:-1],
        )

    # ------------------------------------------------------------------ #
    #  Confirmation prompt — multi-line with per-line delivery date        #
    # ------------------------------------------------------------------ #

    def _generate_confirmation_prompt(self, order_state: OrderState, language: str) -> str:
        if language == "en":
            lines_text = ""
            for i, line in enumerate(order_state.order_lines, 1):
                product_info = f"{line.product_name} ({line.partnum})" if line.partnum else line.product_name
                lines_text += f"  Item {i}   : {line.quantity} {line.unit} {product_info}\n"
                lines_text += f"  Delivery : {line.delivery_date}\n"
                if i < len(order_state.order_lines):
                    lines_text += "  ─────────────────────────────\n"

            return f"""Alright, let me confirm your order:

📦 ORDER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name        : {order_state.customer_name}
Company     : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{lines_text}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Is the information correct to process?

Type:
- "Yes" / "Correct" to confirm
- "Change item [N] [field]" to modify (e.g., "Change item 1 date")
- "Cancel" to cancel"""

        else:
            lines_text = ""
            for i, line in enumerate(order_state.order_lines, 1):
                product_info = f"{line.product_name} ({line.partnum})" if line.partnum else line.product_name
                lines_text += f"  Item {i}     : {line.quantity} {line.unit} {product_info}\n"
                lines_text += f"  Tgl Kirim  : {line.delivery_date}\n"
                if i < len(order_state.order_lines):
                    lines_text += "  ─────────────────────────────\n"

            return f"""Baik, saya konfirmasi pesanan Bapak/Ibu:

📦 DETAIL PESANAN:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Nama        : {order_state.customer_name}
Perusahaan  : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{lines_text}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apakah data sudah benar untuk diproses?

Ketik:
- "Ya" / "Benar" untuk konfirmasi pesanan
- "Ubah item [N] [field]" untuk mengubah (contoh: "Ubah item 1 tanggal")
- "Batal" untuk membatalkan pesanan"""

    # ------------------------------------------------------------------ #
    #  Confirmation response handling                                      #
    # ------------------------------------------------------------------ #

    def _handle_confirmation_response(self, user_message, conversation_id, order_state, language):
        user_input = user_message.lower().strip()

        edit_words = ["ubah", "edit", "ganti", "salah", "change", "modify"]
        if any(w in user_input for w in edit_words):
            return self._handle_edit_request(user_message, conversation_id, order_state, language)

        cancel_words = ["batal", "cancel", "stop", "gak jadi", "tidak jadi"]
        if any(w in user_input for w in cancel_words):
            conversation_manager.reset_order_state(conversation_id)
            msg = (
                "Order cancelled. Is there anything else I can help you with?"
                if language == "en"
                else "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"
            )
            return msg, False

        confirm_words = ["ya", "konfirmasi", "yes", "ok", "oke", "benar", "betul"]
        is_confirm = any(
            user_input == w or user_input.startswith(w + " ") or user_input.endswith(" " + w)
            for w in confirm_words
        )
        if is_confirm:
            return self._complete_order(conversation_id, order_state, language), False

        msg = (
            'Sorry, I didn\'t understand.\n\nType "Yes" to confirm, "Change item [N] [field]" to modify, or "Cancel" to cancel.'
            if language == "en"
            else 'Maaf, saya kurang mengerti.\n\nKetik "Ya" untuk konfirmasi, "Ubah item [N] [field]" untuk mengubah, atau "Batal" untuk membatalkan.'
        )
        return msg, True

    def _handle_edit_request(self, user_message, conversation_id, order_state, language):
        entities = entity_extractor.extract(
            user_message=user_message,
            current_order_state=order_state,
            history=[],
            edit_mode=True,
        )

        if not entities.has_any():
            msg = (
                "Which item and field would you like to change? (e.g., 'change item 1 date to tomorrow')"
                if language == "en"
                else "Item dan field apa yang ingin diubah? (contoh: 'ubah item 1 tanggal jadi besok')"
            )
            return msg, True

        error = self._apply_entities(entities, order_state)
        if error:
            return error, True

        order_state.update_missing_fields()
        conversation_manager.update_order_state(conversation_id, order_state)
        return self._generate_confirmation_prompt(order_state, language), True

    # ------------------------------------------------------------------ #
    #  Complete order                                                       #
    # ------------------------------------------------------------------ #

    def _complete_order(self, conversation_id, order_state, language):
        order_id = self._save_order_to_database(conversation_id, order_state)
        conversation_manager.mark_order_completed(conversation_id)
        conversation_manager.reset_order_state(conversation_id)

        if language == "en":
            lines_text = ""
            for i, line in enumerate(order_state.order_lines, 1):
                lines_text += f"  Item {i}   : {line.quantity} {line.unit} {line.product_name}\n"
                lines_text += f"  Delivery : {line.delivery_date}\n"

            return f"""✅ ORDER SUCCESSFULLY CONFIRMED!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Order Number: {order_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Customer    : {order_state.customer_name}
Company     : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{lines_text}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Thank you! Your order is being processed.
You will receive updates via WhatsApp.

Is there anything else I can help you with?"""

        else:
            lines_text = ""
            for i, line in enumerate(order_state.order_lines, 1):
                lines_text += f"  Item {i}     : {line.quantity} {line.unit} {line.product_name}\n"
                lines_text += f"  Tgl Kirim  : {line.delivery_date}\n"

            return f"""✅ PESANAN BERHASIL DIKONFIRMASI!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Nomor Pesanan: {order_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Customer    : {order_state.customer_name}
Perusahaan  : {order_state.customer_company}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{lines_text}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Terima kasih! Pesanan Anda sedang diproses.
Anda akan menerima update melalui WhatsApp.

Ada yang bisa saya bantu lagi?"""

    def _save_order_to_database(self, conversation_id, order_state) -> str:
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
                    "delivery_date": line.delivery_date,  # per-line date in items JSON
                }
                for line in order_state.order_lines
            ]

            # Order-level delivery_date = earliest across all lines
            dates = [line.delivery_date for line in order_state.order_lines if line.delivery_date]
            earliest_date = min(dates) if dates else None

            phone_number = conversation_manager.get_phone_number(conversation_id)

            new_order = Order(
                order_id=order_id,
                conversation_id=conversation_id,
                customer_name=order_state.customer_name,
                customer_company=order_state.customer_company,
                customer_phone=phone_number,
                delivery_date=earliest_date,
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
        for line in order_lines:
            product = line.get("product_name", "")
            quantity = line.get("quantity", "")
            unit = line.get("unit", "")
            delivery = line.get("delivery_date", "")
            if product:
                entry = f"\n- Produk: {product}"
                if quantity:
                    entry += f", {quantity} {unit}".rstrip()
                if delivery:
                    entry += f", kirim {delivery}"
                order_summary += entry

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

    def handle_resume_response(self, user_message, conversation_id, language):
        user_input = user_message.lower().strip()
        if any(w in user_input for w in ["ya", "lanjut", "iya", "yes", "continue", "ok", "oke"]):
            order_state = conversation_manager.get_order_state(conversation_id)
            context = conversation_manager.get_context(conversation_id)
            return self._ask_for_missing_fields("lanjutkan pesanan", order_state, context, language)
        if any(w in user_input for w in ["baru", "mulai baru", "gak", "tidak", "no", "cancel"]):
            fresh = OrderState()
            fresh.order_status = "new"
            conversation_manager.update_order_state(conversation_id, fresh)
            return "Baik, kita mulai pesanan baru. Produk apa yang ingin Anda pesan?"
        return (
            'Maaf, saya kurang mengerti.\n\n'
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
                f"Maaf, tanggal {delivery_date} sudah lewat ({time_desc}). "
                "Untuk tanggal berapa pengirimannya?"
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


# ── Helper ────────────────────────────────────────────────────────────────────

def _format_missing_for_prompt(missing_fields: list, order_lines: list) -> str:
    """
    Convert structured missing_fields into a readable string for the LLM prompt.
    Groups by line so the bot knows exactly what to ask next.
    """
    if not missing_fields:
        return "Nothing — order is complete."

    order_level = [m for m in missing_fields if m.get("line_index") is None]
    line_level: dict[int, list] = {}
    for m in missing_fields:
        idx = m.get("line_index")
        if idx is not None:
            line_level.setdefault(idx, []).append(m["field"])

    lines = []
    if order_level:
        fields = ", ".join(m["field"] for m in order_level)
        lines.append(f"- Order level: {fields}")
    for idx, fields in sorted(line_level.items()):
        product = None
        if idx < len(order_lines):
            product = order_lines[idx].product_name
        label = f"item {idx + 1}" + (f" ({product})" if product else "")
        lines.append(f"- Line index {idx} / {label}: {', '.join(fields)}")

    return "\n".join(lines)


# Singleton
order_agent = OrderAgent()