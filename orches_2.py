# src/core/orchestrator.py
from src.services.cache_service import cache_store
from src.services.sql_service import sql_service, SessionLocal
from src.services.llm_service import llm_service
from src.services.semantic_search_service import semantic_search_service
from src.core.conversation_manager import conversation_manager
from src.core.intent_classifier import intent_classifier
from src.models.order_state import OrderState, OrderLine
from src.database.sql_schema import Parts
from src.utils.language_detector import language_detector
from src.config.prompts.dialog_prompts import (
    WELCOME_TEMPLATE,
    ORDER_GREETING,
    CANCEL_CONFIRMATION,
    FALLBACK_REDIRECT,
    INVALID_SELECTION
)
import json

class Orchestrator:
    def __init__(self):
        self.cache_service = cache_store
        self.sql_service = sql_service
        self.llm_service = llm_service
        self.semantic_search = semantic_search_service
        self.conversation_manager = conversation_manager
        self.intent_classifier = intent_classifier

        self.current_conversation_id = None
        self.intent_selected = False
        self.current_language = 'id'

        self.awaiting_resume_response = False
        self.awaiting_order_confirmation = False

        # NEW: track human handoff state
        # True = user has requested human handoff, cache is preserved
        self.awaiting_human_handoff = False

        # Warm up cache
        self.warm_up_cache()

    def start_conversation(self, phone_number: str) -> tuple[str, str]:
        """
        Initialize conversation for a user.
        Detects incomplete orders and prompts to resume.

        Returns:
            tuple: (conversation_id, welcome_message)
        """
        conversation_id, order_status, last_order_state = \
            self.conversation_manager.get_or_create_conversation(phone_number)

        self.current_conversation_id = conversation_id
        context = self.conversation_manager.get_context(conversation_id)

        # CASE 1: Incomplete order detected — prompt to resume
        if order_status == "in_progress":
            welcome_message = self._generate_resume_prompt(last_order_state)
            self.conversation_manager.add_message(
                conversation_id=conversation_id,
                role='assistant',
                content=welcome_message
            )
            self.awaiting_resume_response = True
            return conversation_id, welcome_message

        # CASE 2: New conversation or completed previous order
        if len(context) == 0:
            welcome_message = "Halo! Saya Chatbot Asisten mu hari ini! Ada yang bisa saya bantu dengan pemesanan produk?"
            self.conversation_manager.add_message(
                conversation_id=conversation_id,
                role='assistant',
                content=welcome_message
            )
            self.awaiting_resume_response = False
            return conversation_id, welcome_message
        else:
            welcome_message = "Selamat datang kembali! Ada yang bisa saya bantu hari ini?"
            self.awaiting_resume_response = False
            return conversation_id, welcome_message

    def handle_message(self, user_message: str) -> str:
        """Handle incoming user message with Intent Trigger logic"""

        # --- Language detection ---
        detected_lang = language_detector.detect(user_message)
        if self.current_language == 'id' and detected_lang == 'en':
            user_lower = user_message.lower()
            if any(p in user_lower for p in ['speak english', 'talk in english', 'use english', 'english please', 'can we just talk in english']):
                self.current_language = 'en'
        elif self.current_language == 'en' and detected_lang == 'id':
            user_lower = user_message.lower()
            if any(p in user_lower for p in ['bahasa indonesia', 'pakai bahasa indonesia', 'bicara bahasa indonesia']):
                self.current_language = 'id'
        else:
            self.current_language = detected_lang

        context = self.conversation_manager.get_context(self.current_conversation_id)

        # 1. Get current order state
        current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)

        # 2. Classify intent + extract entities
        intent_result = self.intent_classifier.classify_and_extract(user_message, current_order_state)

        print(f"Intent: {intent_result.intent}")
        if intent_result.entities.product_name:
            print(f"🤖 LLM EXTRACTED PRODUCT: '{intent_result.entities.product_name}'")

        # 3. Store user message
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='user',
            content=user_message,
            entities=intent_result.entities.model_dump()
        )

        # 4. Handle resume flow
        if self.awaiting_resume_response:
            response = self._handle_resume_response(user_message)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            self.awaiting_resume_response = False
            return response

        # ---------------------------------------------------------------
        # NEW: Handle HUMAN_HANDOFF intent
        # Must be checked EARLY — before confirmation and other flows —
        # because the user explicitly wants a human regardless of state.
        # ---------------------------------------------------------------
        if intent_result.intent == "HUMAN_HANDOFF":
            response = self._handle_human_handoff(current_order_state)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # ---------------------------------------------------------------
        # NEW: If we are already in handoff state but user sends a new
        # message (e.g. they changed their mind and want to continue),
        # offer to resume the bot conversation.
        # ---------------------------------------------------------------
        if self.awaiting_human_handoff:
            response = self._handle_post_handoff_message(user_message, current_order_state, intent_result)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # 5. Handle order confirmation if awaiting
        if self.awaiting_order_confirmation and current_order_state.is_complete and current_order_state.order_status == "in_progress":
            response = self._handle_confirmation_response(user_message, current_order_state)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response
        elif self.awaiting_order_confirmation:
            self.awaiting_order_confirmation = False

        # 6. CHIT_CHAT
        if intent_result.intent == "CHIT_CHAT":
            context = self.conversation_manager.get_context(self.current_conversation_id)

            if self.current_language == 'en':
                system_prompt = """You are a professional call center customer service representative in Indonesia.

TASK:
Respond naturally and friendly to chit chat or courtesy messages from customers.

STYLE:
- Natural, friendly, and professional
- Brief (1-2 sentences maximum)
- Use polite English

RULES:
- If customer says "thank you" → respond with "You're welcome! Is there anything else I can help you with?"
- If customer says "good morning/afternoon/evening" → return greeting and ask "How can I help you?"
- If customer says "okay/alright/sure" → respond "Alright, thank you"
- If customer says "nothing else/that's all" → respond "Thank you! Don't hesitate to contact us again if you need anything. Have a great day!"
- If customer says "wait/hold on" → respond "Sure, I'll wait"
- Stay professional and not too casual
"""
            else:
                system_prompt = """Anda adalah customer service call center profesional di Indonesia.

TUGAS:
Respond secara natural dan ramah terhadap chit chat atau courtesy message dari customer.

GAYA BICARA:
- Natural, ramah, dan profesional
- Singkat (1-2 kalimat maksimal)
- Gunakan Bahasa Indonesia yang sopan

ATURAN:
- Jika customer bilang "terima kasih" → respond dengan "Sama-sama! Ada yang bisa saya bantu lagi?"
- Jika customer bilang "selamat pagi/siang/sore" → balas greeting dan tanya "Ada yang bisa saya bantu?"
- Jika customer bilang "oke/baik/siap" → respond "Baik, silakan lanjutkan" atau "Terima kasih"
- Jika customer bilang "tidak ada lagi/sudah cukup" → respond "Terima kasih! Jangan ragu hubungi kami lagi jika ada yang dibutuhkan"
- Jika customer bilang "ditunggu ya/sebentar ya" → respond "Baik, saya tunggu"
- Tetap profesional dan jangan terlalu casual
"""

            response = self.llm_service.chat(
                user_message=user_message,
                system_prompt=system_prompt,
                conversation_history=context[-3:]
            )
            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            return response

        # 7. STRICT REDIRECTION for non-order intents (FALLBACK / UNKNOWN)
        if intent_result.intent not in ["ORDER", "CANCEL_ORDER"]:
            user_lower = user_message.lower()
            if any(p in user_lower for p in ['speak english', 'talk in english', 'use english', 'english please', 'can we just talk in english']):
                self.current_language = 'en'
                response = "Of course! I'll continue in English. How can I help you with your order?"
            elif any(p in user_lower for p in ['bahasa indonesia', 'pakai bahasa indonesia', 'bicara bahasa indonesia']):
                self.current_language = 'id'
                response = "Tentu! Saya akan lanjutkan dalam Bahasa Indonesia. Ada yang bisa saya bantu dengan pesanan Anda?"
            elif self.current_language == 'en':
                response = "Sorry, for that assistance or question, please contact our customer service at [Phone Number]. Is there anything else I can help you with regarding orders?"
            else:
                response = "Maaf, untuk bantuan atau pertanyaan tersebut silakan hubungi customer service kami di [Nomor Telepon]. Ada lagi yang bisa saya bantu terkait pemesanan?"

            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            return response

        # 8. CANCEL_ORDER
        if intent_result.intent == "CANCEL_ORDER":
            if current_order_state.order_status == "in_progress":
                self.conversation_manager.reset_order_state(self.current_conversation_id)
                if self.current_language == 'en':
                    response = "Order has been cancelled. Is there anything else I can help you with?"
                else:
                    response = "Pesanan telah dibatalkan. Ada yang bisa saya bantu lagi?"
                self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                return response
            else:
                previous_orders = self.conversation_manager.get_previous_orders(self.current_conversation_id)
                if previous_orders and len(previous_orders) > 0:
                    if self.current_language == 'en':
                        response = "Sorry, for this service we will forward it to our call center. Please wait a moment, we will contact you back at this number"
                    else:
                        response = "Maaf, untuk layanan ini akan saya teruskan ke pihak call center kami. mohon ditunggu sebentar, kami akan menghubungi anda kembali di nomor ini"
                    self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                    return response
                else:
                    if self.current_language == 'en':
                        response = "There is no active order to cancel. Is there anything I can help you with?"
                    else:
                        response = "Tidak ada pesanan aktif yang bisa dibatalkan. Ada yang bisa saya bantu?"
                    self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                    return response

        # 9. Pre-generation check for completed status
        if current_order_state.order_status == "completed":
            context = self.conversation_manager.get_context(self.current_conversation_id)
            response = self._generate_response(current_order_state, user_message, context)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # 9b. Auto-fill customer data from previous orders
        if current_order_state.order_status == "new" or (
            current_order_state.customer_name is None and
            current_order_state.customer_company is None
        ):
            previous_orders = self.conversation_manager.get_previous_orders(self.current_conversation_id)
            if previous_orders and len(previous_orders) > 0:
                last_order = previous_orders[0]
                if current_order_state.customer_name is None and last_order.get('customer_name'):
                    current_order_state.customer_name = last_order['customer_name']
                if current_order_state.customer_company is None and last_order.get('customer_company'):
                    current_order_state.customer_company = last_order['customer_company']
                self.conversation_manager.update_order_state(self.current_conversation_id, current_order_state)
                print(f"✅ Auto-filled customer data from previous order")

        # 9c. Update order state with new entities
        if intent_result.intent == "ORDER" and intent_result.has_entities():
            e = intent_result.entities

            if e.product_name:
                matches = self.semantic_search.search_part_by_description(
                    query=e.product_name, top_k=3, threshold=0.55
                )
                if matches:
                    print(f"\n📋 TOP 3 SEMANTIC SEARCH RESULTS:")
                    for i, match in enumerate(matches[:3], 1):
                        score = match.get('similarity', 0)
                        print(f"   {i}. Score: {score:.4f} | {match['partnum']} | {match['description']}")
                    print()

                if not matches:
                    matches = self.semantic_search.fuzzy_search_by_description(
                        query=e.product_name, top_k=3
                    )

                if matches and len(matches) > 0:
                    best_match = matches[0]
                    if len(current_order_state.order_lines) == 0:
                        current_order_state.order_lines.append(OrderLine())
                    line = current_order_state.order_lines[0]
                    line.partnum = best_match['partnum']
                    line.product_name = best_match['description']
                    line.unit = best_match.get('uom', best_match.get('unit', e.unit))
                    if e.quantity:
                        line.quantity = e.quantity
                else:
                    if len(current_order_state.order_lines) == 0:
                        current_order_state.order_lines.append(OrderLine())
                    line = current_order_state.order_lines[0]
                    line.product_name = e.product_name
                    if e.quantity:
                        line.quantity = e.quantity
                    if e.unit:
                        line.unit = e.unit

            if e.customer_name:
                current_order_state.customer_name = e.customer_name
            if e.customer_company:
                current_order_state.customer_company = e.customer_company
            if e.delivery_date:
                validation_error = self._validate_delivery_date(e.delivery_date)
                if validation_error:
                    self.conversation_manager.add_message(
                        conversation_id=self.current_conversation_id,
                        role='assistant',
                        content=validation_error
                    )
                    return validation_error
                current_order_state.delivery_date = e.delivery_date

            if not e.product_name and len(current_order_state.order_lines) > 0:
                line = current_order_state.order_lines[0]
                if e.quantity:
                    line.quantity = e.quantity
                if e.unit:
                    line.unit = e.unit

            self.conversation_manager.update_order_state(self.current_conversation_id, current_order_state)

        # 10. Trigger confirmation if state just became complete
        current_order_state.update_missing_fields()
        if current_order_state.is_complete and current_order_state.order_status == "in_progress":
            response = self._generate_confirmation_prompt(current_order_state)
            self.awaiting_order_confirmation = True
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # 11. Normal flow — ask for missing fields
        context = self.conversation_manager.get_context(self.current_conversation_id)
        response = self._generate_response(current_order_state, user_message, context)
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='assistant',
            content=response,
            entities=intent_result.entities.model_dump()
        )
        return response

    # ===========================================================
    # NEW: HUMAN HANDOFF HANDLERS
    # ===========================================================

    def _handle_human_handoff(self, current_order_state: OrderState) -> str:
        """
        Handle explicit user request to speak with a human agent.

        Key behaviours:
        - Does NOT reset or delete the order state in cache/DB
        - Builds a context summary so the human agent can see what was
          already captured, reducing repetition for the customer
        - Sets self.awaiting_human_handoff = True so we know the user
          is in this mode and subsequent messages are handled gracefully

        Args:
            current_order_state: Current (possibly partial) order state

        Returns:
            Handoff message string
        """
        # Mark that we are in handoff mode — cache stays intact
        self.awaiting_human_handoff = True

        # Build a brief summary of whatever we already captured so the
        # human agent (and the customer) can see it clearly
        summary = self._build_order_summary_for_handoff(current_order_state)

        if self.current_language == 'en':
            if summary:
                response = (
                    f"Of course! I'll connect you to one of our agents right away.\n\n"
                    f"📋 Here's what I've noted so far:\n{summary}\n\n"
                    f"Our agent will contact you shortly at this number. "
                    f"You don't need to repeat the information above — they'll have it ready. "
                    f"Is there anything else you'd like to add before I transfer you?"
                )
            else:
                response = (
                    "Of course! I'll connect you to one of our agents right away. "
                    "Our agent will contact you shortly at this number. "
                    "Please hold — is there anything you'd like to share before the transfer?"
                )
        else:
            if summary:
                response = (
                    f"Tentu! Saya akan segera menghubungkan Anda ke agen kami.\n\n"
                    f"📋 Informasi yang sudah saya catat:\n{summary}\n\n"
                    f"Agen kami akan menghubungi Anda segera di nomor ini. "
                    f"Anda tidak perlu mengulangi informasi di atas — mereka sudah memilikinya. "
                    f"Ada yang ingin ditambahkan sebelum saya alihkan?"
                )
            else:
                response = (
                    "Tentu! Saya akan segera menghubungkan Anda ke agen kami. "
                    "Agen kami akan menghubungi Anda segera di nomor ini. "
                    "Mohon ditunggu sebentar."
                )

        print(f"🔀 HUMAN_HANDOFF triggered. Order state preserved in cache for conversation: {self.current_conversation_id}")
        return response

    def _build_order_summary_for_handoff(self, order_state: OrderState) -> str:
        """
        Build a human-readable summary of captured order data.
        Returns an empty string if nothing has been captured yet.

        Args:
            order_state: Current order state

        Returns:
            Formatted summary string, or empty string
        """
        lines = []

        if order_state.customer_name:
            lines.append(f"  - Nama       : {order_state.customer_name}")
        if order_state.customer_company:
            lines.append(f"  - Perusahaan : {order_state.customer_company}")
        if order_state.delivery_date:
            lines.append(f"  - Tgl Kirim  : {order_state.delivery_date}")

        if order_state.order_lines:
            line = order_state.order_lines[0]
            if line.product_name:
                product_str = line.product_name
                if line.partnum:
                    product_str += f" ({line.partnum})"
                lines.append(f"  - Produk     : {product_str}")
            if line.quantity:
                qty_str = str(line.quantity)
                if line.unit:
                    qty_str += f" {line.unit}"
                lines.append(f"  - Jumlah     : {qty_str}")

        return "\n".join(lines)

    def _handle_post_handoff_message(self, user_message: str, current_order_state: OrderState, intent_result) -> str:
        """
        Called when awaiting_human_handoff is True and the user sends
        another message. Two sub-cases:
          A) User wants to cancel the handoff and continue with the bot
          B) User is adding info / saying something before the agent arrives

        Args:
            user_message: The new user message
            current_order_state: Current (preserved) order state
            intent_result: Already-classified intent for this message

        Returns:
            Bot response
        """
        user_lower = user_message.lower().strip()

        # Sub-case A: User explicitly wants to go back to the bot
        cancel_handoff_keywords_id = ['batal', 'ga jadi', 'gak jadi', 'tidak jadi', 'lanjut bot', 'balik ke bot', 'bot saja', 'bot aja']
        cancel_handoff_keywords_en = ['cancel', 'nevermind', 'never mind', 'go back', 'back to bot', 'continue with bot']

        wants_to_cancel_handoff = (
            any(k in user_lower for k in cancel_handoff_keywords_id) or
            any(k in user_lower for k in cancel_handoff_keywords_en)
        )

        if wants_to_cancel_handoff:
            # Turn off handoff mode, resume normal bot flow
            self.awaiting_human_handoff = False

            if self.current_language == 'en':
                return (
                    "No problem! I'm back. Let me continue helping you with your order. "
                    "All the information you've provided is still saved. What would you like to do?"
                )
            else:
                return (
                    "Baik! Saya kembali. Semua informasi yang sudah Anda berikan masih tersimpan. "
                    "Mari kita lanjutkan pesanan Anda. Ada yang bisa saya bantu?"
                )

        # Sub-case B: User is sending info or a message while waiting
        # Acknowledge and reassure — do NOT reset handoff flag
        # We still accept ORDER data updates in case they add info
        if intent_result.intent == "ORDER" and intent_result.has_entities():
            # Silently update state so the human agent benefits from it
            e = intent_result.entities
            if e.customer_name:
                current_order_state.customer_name = e.customer_name
            if e.customer_company:
                current_order_state.customer_company = e.customer_company
            if e.delivery_date:
                current_order_state.delivery_date = e.delivery_date
            if e.product_name and len(current_order_state.order_lines) > 0:
                current_order_state.order_lines[0].product_name = e.product_name
            if e.quantity and len(current_order_state.order_lines) > 0:
                current_order_state.order_lines[0].quantity = e.quantity
            if e.unit and len(current_order_state.order_lines) > 0:
                current_order_state.order_lines[0].unit = e.unit

            # Save the updated state
            self.conversation_manager.update_order_state(self.current_conversation_id, current_order_state)

            if self.current_language == 'en':
                return (
                    "Got it! I've noted that information and passed it along to the agent. "
                    "They will contact you shortly."
                )
            else:
                return (
                    "Baik! Informasi tersebut sudah saya catat dan akan diteruskan ke agen. "
                    "Mereka akan segera menghubungi Anda."
                )

        # Generic acknowledgment while in handoff mode
        if self.current_language == 'en':
            return (
                "I've noted your message. Our agent will contact you shortly. "
                "If you'd like to continue with me instead, just type 'go back'."
            )
        else:
            return (
                "Pesan Anda sudah saya catat. Agen kami akan segera menghubungi Anda. "
                "Jika ingin melanjutkan dengan saya, ketik 'batal' atau 'balik ke bot'."
            )

    # ===========================================================
    # All existing methods below — unchanged
    # ===========================================================

    def _generate_response(self, order_state: OrderState, user_message: str, context: list) -> str:
        """Generate LLM response with order state context"""

        is_completed = order_state.order_status == "completed"

        if is_completed:
            if self.current_language == 'en':
                system_prompt = f"""You are a professional call center customer service representative in Indonesia.

        IMPORTANT - ORDER ALREADY COMPLETED:
        - This customer's order is already COMPLETED and cannot be modified
        - You can ONLY provide information about previous orders
        - If customer wants to modify/cancel order, direct them to customer service
        - If customer wants to order again, offer to create a NEW order

        PREVIOUS ORDER INFORMATION (COMPLETED):
        {json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)}

        RULES:
        - Answer questions about previous orders politely
        - If asked to modify/cancel: "Sorry, completed orders cannot be modified. For further assistance, please contact our customer service at [number]. Would you like to create a new order?"
        - Maximum 2-3 sentences per response
        """
            else:
                system_prompt = f"""Anda adalah customer service call center profesional di Indonesia.

        PENTING - PESANAN SUDAH SELESAI:
        - Pesanan customer ini sudah COMPLETED dan tidak bisa diubah
        - Anda HANYA boleh memberikan informasi tentang pesanan sebelumnya
        - Jika customer ingin mengubah/membatalkan pesanan, arahkan ke customer service
        - Jika customer ingin pesan lagi, tawarkan untuk membuat pesanan BARU

        INFORMASI PESANAN SEBELUMNYA (COMPLETED):
        {json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)}

        ATURAN:
        - Jawab pertanyaan tentang pesanan sebelumnya dengan ramah
        - Jika diminta ubah/cancel: "Maaf, pesanan yang sudah selesai tidak bisa diubah. Untuk bantuan lebih lanjut, silakan hubungi customer service kami di [nomor]. Apakah Bapak/Ibu ingin membuat pesanan baru?"
        - Maksimal 2-3 kalimat per respons
        """

        elif order_state.is_complete and order_state.order_status == "in_progress":
            self.awaiting_order_confirmation = True
            return self._generate_confirmation_prompt(order_state)

        else:
            if self.current_language == 'en':
                system_prompt = f"""You are a professional call center customer service representative in Indonesia helping customers order industrial products (gas, parts, etc.).

    SPEAKING STYLE:
    - Use natural English as if speaking directly with the customer
    - Friendly, polite, and professional but not stiff
    - Use "you" or "Sir/Madam"
    - Vary responses, don't be monotonous

    YOUR TASK:
    - Help customers complete order information
    - Ask for missing information naturally
    - Answer customer questions politely
    - Ensure you get: product, quantity, unit, delivery date, customer name, and company/organization name

    IMPORTANT - HOW TO ASK FOR COMPANY NAME:
    - Don't just ask "company name"
    - Ask flexibly: "May I have your full name?" (if no customer_name yet)
    - Ask: "What's the company or organization name?" (if have customer_name but no customer_company)
    - Accept all types: PT, CV, UD, Hospital, Foundation, Cooperative, Store, or individual names
    - If customer gives person name only (e.g., "Jessica"), that's OK for customer_name
    - If customer gives organization (e.g., "Siloam Hospital", "Berkah Store"), that's for customer_company

    CURRENT ORDER INFORMATION:
    {json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)}

    RULES:
    - If customer asks a question, answer it first before continuing
    - Ask for missing/null information one by one
    - If all information is complete, confirm the order
    - Maximum 2-3 sentences per response
    """
            else:
                system_prompt = f"""Anda adalah customer service call center profesional di Indonesia yang sedang membantu pelanggan memesan produk industrial (gas, parts, dll).

    GAYA BICARA:
    - Gunakan Bahasa Indonesia yang natural seperti berbicara langsung dengan pelanggan
    - Ramah, sopan, dan profesional tapi tidak kaku
    - Gunakan kata ganti "Anda" atau "Bapak/Ibu"
    - Variasikan respons, jangan monoton

    TUGAS ANDA:
    - Bantu pelanggan melengkapi informasi pesanan
    - Tanyakan informasi yang masih kurang secara natural
    - Jawab pertanyaan pelanggan dengan ramah
    - Pastikan mendapatkan: produk, jumlah, satuan, tanggal kirim, nama customer, dan nama perusahaan/organisasi

    PENTING - CARA TANYA NAMA PERUSAHAAN:
    - Jangan hanya tanya "nama perusahaan"
    - Tanya dengan fleksibel: "Untuk nama lengkap Bapak/Ibu?" (jika belum ada customer_name)
    - Tanya: "Nama perusahaan atau organisasinya?" (jika sudah ada customer_name tapi belum ada customer_company)
    - Terima semua jenis: PT, CV, UD, Rumah Sakit, Yayasan, Koperasi, Toko, atau nama individu
    - Jika customer bilang nama person saja (misal "Jessica"), itu OK untuk customer_name
    - Jika customer bilang organisasi (misal "RS Siloam", "Toko Berkah"), itu untuk customer_company

    INFORMASI PESANAN SAAT INI:
    {json.dumps(order_state.to_dict(), indent=2, ensure_ascii=False)}

    ATURAN:
    - Jika customer bertanya, jawab dulu pertanyaannya sebelum melanjutkan
    - Tanyakan informasi yang masih kosong/null satu per satu
    - Jika semua informasi lengkap, konfirmasi pesanan
    - Maksimal 2-3 kalimat per respons
    """

        return self.llm_service.chat(
            user_message=user_message,
            system_prompt=system_prompt,
            conversation_history=context[:-1]
        )

    def get_current_order_state(self) -> dict:
        """Get current order state as dict (for debugging/display)"""
        if not self.current_conversation_id:
            return {}
        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        return order_state.to_dict()

    def confirm_and_complete_order(self) -> str:
        """Mark order as completed and save to database"""
        from datetime import datetime
        from src.database.sql_schema import Order

        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)

        if not order_state.is_complete:
            return "Pesanan belum lengkap. Mohon lengkapi informasi yang diperlukan."

        order_id = self._save_order_to_database(order_state)
        self.conversation_manager.mark_order_completed(self.current_conversation_id)
        self.conversation_manager.reset_order_state(self.current_conversation_id)

        order_line = order_state.order_lines[0]

        if self.current_language == 'en':
            confirmation = f"""✅ ORDER SUCCESSFULLY CONFIRMED!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Order Number: {order_id}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Product     : {order_line.product_name}
    Quantity    : {order_line.quantity} {order_line.unit}
    Date        : {order_state.delivery_date}
    Customer    : {order_state.customer_name}
    Company     : {order_state.customer_company}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Thank you! Your order is being processed.
    You will receive updates via WhatsApp.

    Is there anything else I can help you with?"""
        else:
            confirmation = f"""✅ PESANAN BERHASIL DIKONFIRMASI!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Nomor Pesanan: {order_id}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Produk      : {order_line.product_name}
    Jumlah      : {order_line.quantity} {order_line.unit}
    Tanggal     : {order_state.delivery_date}
    Customer    : {order_state.customer_name}
    Perusahaan  : {order_state.customer_company}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Terima kasih! Pesanan Anda sedang diproses.
    Anda akan menerima update melalui WhatsApp.

    Ada yang bisa saya bantu lagi?"""

        return confirmation

    def _save_order_to_database(self, order_state) -> str:
        """Save completed order to database"""
        from datetime import datetime
        from src.database.sql_schema import Order
        from src.services.sql_service import SQLService

        sql_service = SQLService()

        try:
            now = datetime.now()
            date_str = now.strftime("%Y%m%d")

            today_orders = sql_service.db.query(Order).filter(
                Order.order_id.like(f"ORD-{date_str}-%")
            ).count()

            sequence = today_orders + 1
            order_id = f"ORD-{date_str}-{sequence:04d}"

            items = []
            for line in order_state.order_lines:
                items.append({
                    "partnum": line.partnum,
                    "product_name": line.product_name,
                    "quantity": line.quantity,
                    "unit": line.unit
                })

            new_order = Order(
                order_id=order_id,
                conversation_id=self.current_conversation_id,
                customer_name=order_state.customer_name,
                customer_company=order_state.customer_company,
                customer_phone=self.conversation_manager.get_phone_number(self.current_conversation_id),
                delivery_date=order_state.delivery_date,
                status="confirmed",
                items=items,
                created_at=now,
                updated_at=now
            )

            sql_service.db.add(new_order)
            sql_service.db.commit()

            print(f"✅ Order saved to database: {order_id}")
            return order_id

        except Exception as e:
            print(f"❌ Error saving order to database: {e}")
            sql_service.db.rollback()
            return f"ORD-{datetime.now().strftime('%Y%m%d')}-TEMP"

        finally:
            sql_service.close()

    def _generate_confirmation_prompt(self, order_state: OrderState) -> str:
        """Generate order confirmation prompt when all fields are complete"""
        order_line = order_state.order_lines[0]

        product_info = order_line.product_name
        if order_line.partnum:
            product_info = f"{order_line.product_name} ({order_line.partnum})"

        confirmation = f"""Baik, saya konfirmasi pesanan Bapak/Ibu:

📦 DETAIL PESANAN:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Produk      : {product_info}
Jumlah      : {order_line.quantity} {order_line.unit}
Nama        : {order_state.customer_name}
Perusahaan  : {order_state.customer_company}
Tanggal     : {order_state.delivery_date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Apakah data sudah benar untuk diproses?

Ketik:
- "Ya" / "Benar" untuk konfirmasi pesanan
- "Ubah [field]" untuk mengubah (contoh: "Ubah tanggal")
- "Batal" untuk membatalkan pesanan"""

        if self.current_language == 'en':
            confirmation = f"""Alright, let me confirm your order:

📦 ORDER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Product     : {product_info}
Quantity    : {order_line.quantity} {order_line.unit}
Name        : {order_state.customer_name}
Company     : {order_state.customer_company}
Date        : {order_state.delivery_date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Is the information correct to process?

Type:
- "Yes" / "Correct" to confirm order
- "Change [field]" to modify (example: "Change date")
- "Cancel" to cancel order"""

        return confirmation

    def _handle_confirmation_response(self, user_message: str, order_state: OrderState) -> str:
        """Handle user's response to order confirmation prompt"""
        user_input = user_message.lower().strip()

        confirmation_words = ['ya', 'konfirmasi', 'yes', 'ok', 'oke', 'benar', 'betul']
        if any(user_input == word or user_input.startswith(word + ' ') or user_input.endswith(' ' + word) for word in confirmation_words):
            response = self.confirm_and_complete_order()
            self.awaiting_order_confirmation = False
            return response

        elif any(word in user_input for word in ['batal', 'cancel', 'stop', 'gak jadi', 'tidak jadi']):
            self.conversation_manager.reset_order_state(self.current_conversation_id)
            self.awaiting_order_confirmation = False
            if self.current_language == 'en':
                return "Order cancelled. Thank you. Is there anything else I can help you with?"
            else:
                return "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"

        elif 'ubah' in user_input or 'edit' in user_input or 'ganti' in user_input or 'salah' in user_input or 'change' in user_input or 'modify' in user_input:
            changes_result = self._extract_order_changes(user_message, order_state)

            if changes_result['has_changes']:
                updated = self._apply_order_changes(order_state, changes_result['changes'])

                if isinstance(updated, dict) and 'error' in updated:
                    return updated['error']

                if updated:
                    order_state.update_missing_fields()
                    self.conversation_manager.update_order_state(self.current_conversation_id, order_state)
                    self.awaiting_order_confirmation = True
                    return self._generate_confirmation_prompt(order_state)
                else:
                    if self.current_language == 'en':
                        return "Sorry, I couldn't understand the changes you want. Could you explain in more detail?"
                    else:
                        return "Maaf, saya tidak bisa memahami perubahan yang Anda inginkan. Bisa dijelaskan lebih detail?"
            else:
                if self.current_language == 'en':
                    return "Alright, which field would you like to change? (example: 'change date to tomorrow', 'change company to CV ABC')"
                else:
                    return "Baik, field apa yang ingin diubah? (contoh: 'ubah tanggal jadi besok', 'ganti perusahaan jadi CV ABC')"

        else:
            if self.current_language == 'en':
                return """Sorry, I don't quite understand.

Is the order information correct?
Type:
- "Yes" to confirm
- "Change [field] to [value]" to modify
- "Cancel" to cancel"""
            else:
                return """Maaf, saya kurang mengerti.

Apakah data pesanan sudah benar?
Ketik:
- "Ya" untuk konfirmasi
- "Ubah [field] jadi [value]" untuk mengubah
- "Batal" untuk membatalkan"""

    def _extract_order_changes(self, user_message: str, current_order_state: OrderState) -> dict:
        """Use LLM to extract order changes from natural language"""
        from datetime import datetime

        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_day = now.strftime("%A")
        current_day_id = {
            "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
            "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"
        }.get(current_day, current_day)

        system_prompt = f"""Anda adalah sistem ekstraksi perubahan pesanan.

CURRENT_DATE: {current_date} ({current_day_id})

TUGAS:
Ekstrak perubahan yang diminta user dari pesanan yang sudah ada.

CURRENT ORDER STATE:
{json.dumps(current_order_state.to_dict(), indent=2, ensure_ascii=False)}

USER MESSAGE:
"{user_message}"

OUTPUT FORMAT (JSON):
{{
  "has_changes": true/false,
  "changes": {{
    "customer_name": "nilai baru" atau null (jika tidak diubah),
    "customer_company": "nilai baru" atau null,
    "delivery_date": "YYYY-MM-DD" atau null,
    "product_name": "nilai baru" atau null,
    "quantity": angka atau null,
    "unit": "M3/BTL/TABUNG" atau null
  }}
}}

ATURAN:
1. Jika user menyebut "besok" → CURRENT_DATE + 1 hari
2. Jika user menyebut "lusa" → CURRENT_DATE + 2 hari
3. Jika user menyebut tanggal spesifik → konversi ke YYYY-MM-DD
4. Hanya isi field yang DIUBAH, sisanya null
5. Jika tidak ada perubahan jelas → has_changes: false"""

        try:
            llm_response = self.llm_service.chat(
                user_message=user_message,
                system_prompt=system_prompt,
                conversation_history=[]
            )
            result = json.loads(llm_response)
            return result
        except Exception as e:
            print(f"⚠️ Error extracting changes: {e}")
            return {"has_changes": False, "changes": {}}

    def _apply_order_changes(self, order_state: OrderState, changes: dict) -> bool:
        """Apply extracted changes to order state"""
        applied = False

        if changes.get('customer_name'):
            order_state.customer_name = changes['customer_name']
            applied = True
            print(f"✏️ Updated customer_name: {changes['customer_name']}")

        if changes.get('customer_company'):
            order_state.customer_company = changes['customer_company']
            applied = True
            print(f"✏️ Updated customer_company: {changes['customer_company']}")

        if changes.get('delivery_date'):
            validation_error = self._validate_delivery_date(changes['delivery_date'])
            if validation_error:
                return {"error": validation_error}
            order_state.delivery_date = changes['delivery_date']
            applied = True
            print(f"✏️ Updated delivery_date: {changes['delivery_date']}")

        if len(order_state.order_lines) > 0:
            if changes.get('product_name'):
                matches = self.semantic_search.search_part_by_description(
                    query=changes['product_name'], top_k=3, threshold=0.55
                )
                if matches and len(matches) > 0:
                    best_match = matches[0]
                    order_state.order_lines[0].product_name = best_match['description']
                    order_state.order_lines[0].partnum = best_match['partnum']
                    applied = True
                    print(f"✏️ Updated product: {best_match['description']}")

            if changes.get('quantity'):
                order_state.order_lines[0].quantity = changes['quantity']
                applied = True
                print(f"✏️ Updated quantity: {changes['quantity']}")

            if changes.get('unit'):
                order_state.order_lines[0].unit = changes['unit']
                applied = True
                print(f"✏️ Updated unit: {changes['unit']}")

        return applied

    def _generate_resume_prompt(self, last_order_state: dict) -> str:
        """Generate friendly prompt to ask user if they want to resume incomplete order"""
        customer_name = last_order_state.get('customer_name', '')
        order_lines = last_order_state.get('order_lines', [])

        order_summary = ""
        if order_lines and len(order_lines) > 0:
            line = order_lines[0]
            product = line.get('product_name', '')
            quantity = line.get('quantity', '')
            unit = line.get('unit', '')

            if product:
                order_summary = f"\n- Produk: {product}"
                if quantity:
                    order_summary += f"\n- Jumlah: {quantity} {unit}" if unit else f"\n- Jumlah: {quantity}"

        greeting = f"Halo {customer_name}!" if customer_name else "Halo!"

        if order_summary:
            message = f"""{greeting} Sepertinya pesanan Anda sebelumnya:{order_summary}

    belum selesai. Apakah ingin melanjutkan pesanan ini?

    Ketik:
    - "Ya" / "Lanjut" untuk melanjutkan
    - "Mulai Baru" untuk membuat pesanan baru"""
        else:
            message = f"""{greeting} Sepertinya Anda memiliki pesanan yang belum selesai.

    Apakah ingin melanjutkan pesanan sebelumnya?

    Ketik:
    - "Ya" / "Lanjut" untuk melanjutkan
    - "Mulai Baru" untuk membuat pesanan baru"""

        return message

    def _handle_resume_response(self, user_message: str) -> str:
        """Handle user's response to resume prompt"""
        user_input = user_message.lower().strip()

        if any(word in user_input for word in ['ya', 'lanjut', 'iya', 'yes', 'continue', 'ok', 'oke']):
            current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
            context = self.conversation_manager.get_context(self.current_conversation_id)
            return self._generate_response(current_order_state, "lanjutkan pesanan", context)

        elif any(word in user_input for word in ['baru', 'mulai baru', 'gak', 'tidak', 'no', 'cancel']):
            new_order_state = OrderState()
            new_order_state.order_status = "new"
            self.conversation_manager.update_order_state(self.current_conversation_id, new_order_state)
            return "Baik, kita mulai pesanan baru. Produk apa yang ingin Anda pesan?"

        else:
            return """Maaf, saya kurang mengerti.

    Apakah Anda ingin melanjutkan pesanan sebelumnya?
    Ketik "Ya" untuk melanjutkan atau "Mulai Baru" untuk pesanan baru."""

    def _validate_delivery_date(self, delivery_date: str) -> str:
        """Validate delivery date"""
        from datetime import datetime
        import pytz

        try:
            delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
        except ValueError:
            return "Maaf, format tanggal tidak valid. Mohon berikan tanggal dalam format yang jelas (contoh: 'besok', '15 Februari', dll)."

        wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(wib)
        today = now_wib.date()
        delivery_date_obj = delivery_dt.date()

        if delivery_date_obj < today:
            days_ago = (today - delivery_date_obj).days
            if days_ago == 1:
                time_desc = "kemarin"
            elif days_ago == 2:
                time_desc = "kemarin lusa"
            else:
                time_desc = f"{days_ago} hari yang lalu"
            return f"Maaf, tanggal {delivery_date} itu sudah lewat ({time_desc}). Untuk tanggal berapa ya pengirimannya?"

        if delivery_dt.weekday() == 6:
            date_formatted = delivery_dt.strftime("%d %B %Y")
            month_map = {
                "January": "Januari", "February": "Februari", "March": "Maret",
                "April": "April", "May": "Mei", "June": "Juni",
                "July": "Juli", "August": "Agustus", "September": "September",
                "October": "Oktober", "November": "November", "December": "Desember"
            }
            for eng, ind in month_map.items():
                date_formatted = date_formatted.replace(eng, ind)
            return f"Maaf, tanggal {date_formatted} itu hari Minggu. Kami tidak melayani pengiriman di hari Minggu. Bisa pilih tanggal lain?"

        return None

    def warm_up_cache(self):
        """Load all parts into cache for fast semantic search"""
        print("Warming up cache with customer data...")
        db = SessionLocal()
        parts = db.query(Parts).all()
        for c in parts:
            cache_store.set(c.id, {
                "id": c.id,
                "partnum": c.partnum,
                "description": c.description,
                "uom": c.uom,
                "uomdesc": c.uomdesc,
                "embedding": c.embedding
            })
        db.close()
        print(f"Cache ready with {len(parts)} records.")

    def debug_cache(self):
        """Debug: Print cache contents"""
        print("\n" + "="*50)
        print("🔍 CACHE CONTENTS")
        print("="*50)

        all_keys = list(self.cache_service._cache.keys())
        print(f"\n📋 Total keys in cache: {len(all_keys)}")

        order_states = [k for k in all_keys if isinstance(k, str) and k.startswith("order_state:")]
        contexts = [k for k in all_keys if isinstance(k, str) and k.startswith("context:")]
        customers = [k for k in all_keys if isinstance(k, str) and k.startswith("customer:")]
        products = [k for k in all_keys if isinstance(k, int)]

        print(f"\n📦 Products cached: {len(products)}")
        print(f"💬 Conversations cached: {len(contexts)}")
        print(f"📝 Order states cached: {len(order_states)}")
        print(f"👤 Customers cached: {len(customers)}")

        if self.current_conversation_id:
            print(f"\n🎯 CURRENT CONVERSATION: {self.current_conversation_id}")

            order_state_key = f"order_state:{self.current_conversation_id}"
            if order_state_key in self.cache_service._cache:
                print(f"\n📝 Order State:")
                print(json.dumps(self.cache_service._cache[order_state_key], indent=2, ensure_ascii=False))

            context_key = f"context:{self.current_conversation_id}"
            if context_key in self.cache_service._cache:
                print(f"\n💬 Conversation Context (last {len(self.cache_service._cache[context_key])} messages):")
                for msg in self.cache_service._cache[context_key]:
                    print(f"  {msg['role']:10s}: {msg['content'][:60]}...")

            # NEW: Show handoff state
            print(f"\n🔀 Handoff state: {'ACTIVE (waiting for agent)' if self.awaiting_human_handoff else 'None'}")

        print("="*50 + "\n")