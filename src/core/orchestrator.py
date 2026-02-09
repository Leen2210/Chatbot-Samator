# src/core/orchestrator.py
from src.services.cache_service import cache_store
from src.services.sql_service import sql_service, SessionLocal
from src.services.llm_service import llm_service
from src.services.semantic_search_service import semantic_search_service
from src.core.conversation_manager import conversation_manager
from src.core.intent_classifier import intent_classifier
from src.models.order_state import OrderState, OrderLine
from src.database.sql_schema import Parts
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
        self.intent_selected = False  # Track if user has selected intent

        self.awaiting_resume_response = False  # 🆕 Track if waiting for resume answer
        self.awaiting_order_confirmation = False  # 🆕 Track if waiting for order confirmation


        # Warm up cache
        self.warm_up_cache()

    def start_conversation(self, phone_number: str) -> tuple[str, str]:
        """
        Initialize conversation for a user
        Detects incomplete orders and prompts to resume

        Returns:
            tuple: (conversation_id, welcome_message)
        """
        # 🆕 Get conversation with status info
        conversation_id, order_status, last_order_state = \
            self.conversation_manager.get_or_create_conversation(phone_number)

        self.current_conversation_id = conversation_id

        # Get conversation context
        context = self.conversation_manager.get_context(conversation_id)

        # 🆕 CASE 1: Incomplete order detected - Prompt to resume
        if order_status == "in_progress":
            welcome_message = self._generate_resume_prompt(last_order_state)

            # Store the resume prompt
            self.conversation_manager.add_message(
                conversation_id=conversation_id,
                role='assistant',
                content=welcome_message
            )

            # Set flag to track resume mode
            self.awaiting_resume_response = True

            return conversation_id, welcome_message

        # 🆕 CASE 2: New conversation or completed previous order
        if len(context) == 0:
            # Brand new conversation
            welcome_message = "Halo! Saya Chatbot Asisten mu hari ini! Ada yang bisa saya bantu dengan pemesanan produk?"

            self.conversation_manager.add_message(
                conversation_id=conversation_id,
                role='assistant',
                content=welcome_message
            )

            self.awaiting_resume_response = False

            return conversation_id, welcome_message
        else:
            # Returning user with completed order
            welcome_message = "Selamat datang kembali! Ada yang bisa saya bantu hari ini?"

            self.awaiting_resume_response = False

            return conversation_id, welcome_message

    def handle_message(self, user_message: str) -> str:
        """Handle incoming user message with Intent Trigger logic"""
        context = self.conversation_manager.get_context(self.current_conversation_id)

        # 1. Get current order state from Cache/DB
        current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)

        # 2. CALL INTENT CLASSIFIER (The Trigger)
        # Identify user intent and extract entities based on current state
        intent_result = self.intent_classifier.classify_and_extract(user_message, current_order_state)

        print(f"Intent: {intent_result.intent}")
        # 3. Store user message with extracted entities for DB visibility
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='user',
            content=user_message,
            entities=intent_result.entities.model_dump()
        )

        # 4. Handle Special Flow: Resume incomplete order
        if self.awaiting_resume_response:
            response = self._handle_resume_response(user_message)
            self.conversation_manager.add_message(
                self.current_conversation_id, 'assistant', response
            )
            self.awaiting_resume_response = False
            return response

        # 5. PRIORITY: Handle order confirmation if awaiting
        # This must come BEFORE intent checks to prevent "ya" being classified as CHIT_CHAT
        if self.awaiting_order_confirmation and current_order_state.is_complete and current_order_state.order_status == "in_progress":
            response = self._handle_confirmation_response(user_message, current_order_state)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response
        elif self.awaiting_order_confirmation:
            # Flag is set but order is not ready for confirmation - reset flag
            self.awaiting_order_confirmation = False

        # 6. CHIT_CHAT: Handle courtesy responses and casual conversation
        if intent_result.intent == "CHIT_CHAT":
            # Use LLM to generate natural response
            context = self.conversation_manager.get_context(self.current_conversation_id)

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

CONTOH:
User: "terima kasih"
Bot: "Sama-sama! Ada yang bisa saya bantu lagi?"

User: "selamat siang"
Bot: "Selamat siang! Ada yang bisa saya bantu hari ini?"

User: "oke siap"
Bot: "Baik, terima kasih. Silakan lanjutkan jika ada yang dibutuhkan."

User: "tidak ada lagi, makasih"
Bot: "Terima kasih sudah menghubungi kami! Jangan ragu chat lagi jika ada yang dibutuhkan. Selamat beraktivitas!"
"""

            response = self.llm_service.chat(
                user_message=user_message,
                system_prompt=system_prompt,
                conversation_history=context[-3:]  # Last 3 messages for context
            )

            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            return response

        # 6. STRICT REDIRECTION: If intent is not ORDER or CANCEL, redirect to Call Center
        if intent_result.intent not in ["ORDER", "CANCEL_ORDER"]:
            response = "Maaf, untuk bantuan atau pertanyaan tersebut silakan hubungi customer service kami di [Nomor Telepon]. Ada lagi yang bisa saya bantu terkait pemesanan?"

            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            return response

        # 7. CANCELATION
        if intent_result.intent == "CANCEL_ORDER":
            # 🔍 Check if there are any completed orders in database
            previous_orders = self.conversation_manager.get_previous_orders(self.current_conversation_id)

            # If user has completed orders, they can't cancel via chatbot
            if previous_orders and len(previous_orders) > 0:
                response = "Maaf, untuk layanan ini akan saya teruskan ke pihak call center kami. mohon ditunggu sebentar, kami akan menghubungi anda kembali di nomor ini"
                self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                return response

            # Check if current order is in progress
            elif current_order_state.order_status == "in_progress":
                # 🗑️ Reset order state (buang pesanan yang dibatalkan)
                self.conversation_manager.reset_order_state(self.current_conversation_id)

                response = "Pesanan telah dibatalkan. Ada yang bisa saya bantu lagi?"
                self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                return response

            else:
                # No active order to cancel
                response = "Tidak ada pesanan aktif yang bisa dibatalkan. Ada yang bisa saya bantu?"
                self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
                return response

            

           
        # 8. PRE-GENERATION CHECK: Check for completed status
        if current_order_state.order_status == "completed":
            context = self.conversation_manager.get_context(self.current_conversation_id)
            response = self._generate_response(current_order_state, user_message, context)
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # 8b. PRE-FILL: Auto-fill customer data from previous orders if available
        if current_order_state.order_status == "new" or (
            current_order_state.customer_name is None and
            current_order_state.customer_company is None
        ):
            # Check if we have previous order data in conversation history
            previous_orders = self.conversation_manager.get_previous_orders(self.current_conversation_id)
            if previous_orders and len(previous_orders) > 0:
                last_order = previous_orders[0]  # Most recent order
                if current_order_state.customer_name is None and last_order.get('customer_name'):
                    current_order_state.customer_name = last_order['customer_name']
                if current_order_state.customer_company is None and last_order.get('customer_company'):
                    current_order_state.customer_company = last_order['customer_company']

                # Update in database
                self.conversation_manager.update_order_state(
                    self.current_conversation_id,
                    current_order_state
                )
                print(f"✅ Auto-filled customer data from previous order")

        # 8c. UPDATE ORDER STATE: Apply new data to the state object
        if intent_result.intent == "ORDER" and intent_result.has_entities():
            e = intent_result.entities

            # 🆕 SEMANTIC SEARCH: Match product to database using embeddings
            if e.product_name:
                # Try semantic search first
                matches = self.semantic_search.search_part_by_description(
                    query=e.product_name,
                    top_k=3,
                    threshold=0.55  # 55% minimum similarity
                )

                # If no semantic matches, try fuzzy search
                if not matches:
                    matches = self.semantic_search.fuzzy_search_by_description(
                        query=e.product_name,
                        top_k=3
                    )

                # Handle matches - ALWAYS auto-select best match
                if matches and len(matches) > 0:
                    best_match = matches[0]

                    # Create order line if not exists
                    if len(current_order_state.order_lines) == 0:
                        current_order_state.order_lines.append(OrderLine())

                    # Auto-select best match (no user selection needed)
                    line = current_order_state.order_lines[0]
                    line.partnum = best_match['partnum']
                    line.product_name = best_match['description']
                    line.unit = best_match.get('uom', best_match.get('unit', e.unit))
                    if e.quantity: line.quantity = e.quantity

                # No matches: use raw text
                else:
                    if len(current_order_state.order_lines) == 0:
                        current_order_state.order_lines.append(OrderLine())

                    line = current_order_state.order_lines[0]
                    line.product_name = e.product_name
                    if e.quantity: line.quantity = e.quantity
                    if e.unit: line.unit = e.unit

            # Map other fields to order_state
            if e.customer_name: current_order_state.customer_name = e.customer_name
            if e.customer_company: current_order_state.customer_company = e.customer_company
            if e.delivery_date:
                # Validate delivery date before setting
                validation_error = self._validate_delivery_date(e.delivery_date)
                if validation_error:
                    # Return error message to user
                    self.conversation_manager.add_message(
                        conversation_id=self.current_conversation_id,
                        role='assistant',
                        content=validation_error
                    )
                    return validation_error

                current_order_state.delivery_date = e.delivery_date

            # Update quantity/unit if no product_name was extracted
            if not e.product_name and len(current_order_state.order_lines) > 0:
                line = current_order_state.order_lines[0]
                if e.quantity: line.quantity = e.quantity
                if e.unit: line.unit = e.unit

            # This triggers the cache update
            self.conversation_manager.update_order_state(
                self.current_conversation_id,
                current_order_state
            )

        # 9. TRIGGER CONFIRMATION: If state just became complete
        current_order_state.update_missing_fields()
        if current_order_state.is_complete and current_order_state.order_status == "in_progress":
            response = self._generate_confirmation_prompt(current_order_state)
            self.awaiting_order_confirmation = True
            self.conversation_manager.add_message(self.current_conversation_id, 'assistant', response)
            return response

        # 10. NORMAL FLOW: Generate LLM response asking for missing fields
        context = self.conversation_manager.get_context(self.current_conversation_id)
        response = self._generate_response(current_order_state, user_message, context)

        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='assistant',
            content=response,
            entities=intent_result.entities.model_dump()
        )

        return response

    def _generate_response(self, order_state: OrderState, user_message: str, context: list) -> str:
        """
        Generate LLM response with order state context
        """

        # 🆕 Check if dealing with completed order
        is_completed = order_state.order_status == "completed"

        # 🆕 Build different system prompts based on order status
        if is_completed:
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
            # Generate confirmation prompt instead of asking LLM
            # 🔥 IMPORTANT: Set flag to await confirmation
            self.awaiting_order_confirmation = True
            return self._generate_confirmation_prompt(order_state)

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
    - Pastikan mendapatkan: produk, jumlah, satuan, tanggal kirim, nama customer, dan perusahaan

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
            conversation_history=context[:-1]  # Exclude current message
        )


    def get_current_order_state(self) -> dict:
        """Get current order state as dict (for debugging/display)"""
        if not self.current_conversation_id:
            return {}

        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        return order_state.to_dict()

    def confirm_and_complete_order(self) -> str:
        """
        Mark order as completed and save to database
        This should be called after user confirms the order

        Returns:
            Confirmation message
        """
        from datetime import datetime
        from src.database.sql_schema import Order

        # Get current order state
        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)

        # Validate order is complete
        if not order_state.is_complete:
            return "Pesanan belum lengkap. Mohon lengkapi informasi yang diperlukan."

        # 💾 SAVE ORDER TO DATABASE
        order_id = self._save_order_to_database(order_state)

        # Mark as completed (locks from further edits)
        self.conversation_manager.mark_order_completed(self.current_conversation_id)

        # 🔄 RESET ORDER STATE for new order
        self.conversation_manager.reset_order_state(self.current_conversation_id)

        # Generate confirmation message
        order_line = order_state.order_lines[0]

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
        """
        Save completed order to database

        Args:
            order_state: Completed order state

        Returns:
            order_id: Generated order ID (e.g., ORD-20260209-0001)
        """
        from datetime import datetime
        from src.database.sql_schema import Order
        from src.services.sql_service import SQLService

        sql_service = SQLService()

        try:
            # Generate unique order ID
            now = datetime.now()
            date_str = now.strftime("%Y%m%d")  # 20260209

            # Get count of orders today to generate sequence number
            today_orders = sql_service.db.query(Order).filter(
                Order.order_id.like(f"ORD-{date_str}-%")
            ).count()

            sequence = today_orders + 1
            order_id = f"ORD-{date_str}-{sequence:04d}"  # ORD-20260209-0001

            # Prepare items JSON
            items = []
            for line in order_state.order_lines:
                items.append({
                    "partnum": line.partnum,
                    "product_name": line.product_name,
                    "quantity": line.quantity,
                    "unit": line.unit
                })

            # Create order record
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
            # Return fallback order ID
            return f"ORD-{datetime.now().strftime('%Y%m%d')}-TEMP"

        finally:
            sql_service.close()

    def _generate_confirmation_prompt(self, order_state: OrderState) -> str:
        """
        Generate order confirmation prompt when all fields are complete

        Args:
            order_state: Complete order state

        Returns:
            Confirmation message
        """
        order_line = order_state.order_lines[0]

        # Build product info with part number if available
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

        return confirmation

    def _handle_confirmation_response(self, user_message: str, order_state: OrderState) -> str:
        """
        Handle user's response to order confirmation prompt

        Args:
            user_message: User's response
            order_state: Current order state

        Returns:
            Bot response
        """
        user_input = user_message.lower().strip()

        # 🆕 Option 1: User confirms (Ya/Konfirmasi/OK) - STRICT CHECK
        # Must be standalone word, not part of other words like "aja"
        confirmation_words = ['ya', 'konfirmasi', 'yes', 'ok', 'oke', 'benar', 'betul']
        if any(user_input == word or user_input.startswith(word + ' ') or user_input.endswith(' ' + word) for word in confirmation_words):
            # Complete the order
            response = self.confirm_and_complete_order()
            self.awaiting_order_confirmation = False
            return response

        # 🆕 Option 2: User wants to cancel (Batal)
        elif any(word in user_input for word in ['batal', 'cancel', 'stop', 'gak jadi', 'tidak jadi']):
            # 🗑️ Reset order state (buang pesanan yang dibatalkan)
            self.conversation_manager.reset_order_state(self.current_conversation_id)

            self.awaiting_order_confirmation = False

            return "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"

        # 🆕 Option 3: User wants to edit (Ubah/Ganti/Edit)
        elif 'ubah' in user_input or 'edit' in user_input or 'ganti' in user_input or 'salah' in user_input:
            # 🔥 NEW: Use LLM to extract changes from natural language
            changes_result = self._extract_order_changes(user_message, order_state)

            if changes_result['has_changes']:
                # Apply changes to order state
                updated = self._apply_order_changes(order_state, changes_result['changes'])

                # Check if there was a validation error
                if isinstance(updated, dict) and 'error' in updated:
                    # Return validation error to user
                    return updated['error']

                if updated:
                    # Update order state
                    order_state.update_missing_fields()
                    self.conversation_manager.update_order_state(
                        self.current_conversation_id,
                        order_state
                    )

                    # Show updated confirmation
                    self.awaiting_order_confirmation = True
                    return self._generate_confirmation_prompt(order_state)
                else:
                    return "Maaf, saya tidak bisa memahami perubahan yang Anda inginkan. Bisa dijelaskan lebih detail?"
            else:
                # No clear changes detected - ask for clarification
                return "Baik, field apa yang ingin diubah? (contoh: 'ubah tanggal jadi besok', 'ganti perusahaan jadi CV ABC')"

        # 🆕 Option 4: Unclear response - ask again
        else:
            return """Maaf, saya kurang mengerti.

Apakah data pesanan sudah benar?
Ketik:
- "Ya" untuk konfirmasi
- "Ubah [field] jadi [value]" untuk mengubah
- "Batal" untuk membatalkan"""

    def _extract_order_changes(self, user_message: str, current_order_state: OrderState) -> dict:
        """
        Use LLM to extract order changes from natural language

        Args:
            user_message: User's message (e.g., "ubah perusahaan jadi CV Surya Dadi dan tanggal jadi besok")
            current_order_state: Current order state

        Returns:
            dict with 'has_changes' and 'changes' keys
        """
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
5. Jika tidak ada perubahan jelas → has_changes: false

CONTOH:
User: "ubah perusahaan jadi CV Surya Dadi dan tanggal jadi besok"
Output:
{{
  "has_changes": true,
  "changes": {{
    "customer_name": null,
    "customer_company": "CV Surya Dadi",
    "delivery_date": "2026-02-10",
    "product_name": null,
    "quantity": null,
    "unit": null
  }}
}}"""

        try:
            llm_response = self.llm_service.chat(
                user_message=user_message,
                system_prompt=system_prompt,
                conversation_history=[]
            )

            # Parse JSON from LLM
            result = json.loads(llm_response)
            return result

        except Exception as e:
            print(f"⚠️ Error extracting changes: {e}")
            return {"has_changes": False, "changes": {}}

    def _apply_order_changes(self, order_state: OrderState, changes: dict) -> bool:
        """
        Apply extracted changes to order state

        Args:
            order_state: Current order state (will be modified in place)
            changes: Dict of changes from _extract_order_changes

        Returns:
            True if any changes were applied, False otherwise
        """
        applied = False

        # Apply customer name change
        if changes.get('customer_name'):
            order_state.customer_name = changes['customer_name']
            applied = True
            print(f"✏️ Updated customer_name: {changes['customer_name']}")

        # Apply customer company change
        if changes.get('customer_company'):
            order_state.customer_company = changes['customer_company']
            applied = True
            print(f"✏️ Updated customer_company: {changes['customer_company']}")

        # Apply delivery date change (with validation)
        if changes.get('delivery_date'):
            # Validate date first
            validation_error = self._validate_delivery_date(changes['delivery_date'])
            if validation_error:
                # Return error in a special way that caller can detect
                return {"error": validation_error}

            order_state.delivery_date = changes['delivery_date']
            applied = True
            print(f"✏️ Updated delivery_date: {changes['delivery_date']}")

        # Apply product changes
        if len(order_state.order_lines) > 0:
            if changes.get('product_name'):
                # Need to do semantic search for new product
                search_result = self.semantic_search_service.search_part_by_description(
                    changes['product_name']
                )
                if search_result and search_result['similarity'] > 0.65:
                    order_state.order_lines[0].product_name = search_result['description']
                    order_state.order_lines[0].partnum = search_result['partnum']
                    applied = True
                    print(f"✏️ Updated product: {search_result['description']}")

            if changes.get('quantity'):
                order_state.order_lines[0].quantity = changes['quantity']
                applied = True
                print(f"✏️ Updated quantity: {changes['quantity']}")

            if changes.get('unit'):
                order_state.order_lines[0].unit = changes['unit']
                applied = True
                print(f"✏️ Updated unit: {changes['unit']}")

        return applied

    #RESPONSE FOR RESUME CHAT
    def _generate_resume_prompt(self, last_order_state: dict) -> str:
        """
        Generate friendly prompt to ask user if they want to resume incomplete order

        Args:
            last_order_state: Dictionary of last order state

        Returns:
            Resume prompt message
        """
        # Extract info from last order
        customer_name = last_order_state.get('customer_name', '')
        order_lines = last_order_state.get('order_lines', [])

        # Build order summary
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

        # Build greeting
        greeting = f"Halo {customer_name}!" if customer_name else "Halo!"

        # Build full prompt
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
        """
        Handle user's response to resume prompt

        Args:
            user_message: User's response

        Returns:
            Bot response
        """
        user_input = user_message.lower().strip()

        # Check if user wants to continue
        if any(word in user_input for word in ['ya', 'lanjut', 'iya', 'yes', 'continue', 'ok', 'oke']):
            # User wants to continue - keep existing order_state
            current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)

            # Generate response asking for missing fields
            context = self.conversation_manager.get_context(self.current_conversation_id)
            return self._generate_response(current_order_state, "lanjutkan pesanan", context)

        # Check if user wants to start fresh
        elif any(word in user_input for word in ['baru', 'mulai baru', 'gak', 'tidak', 'no', 'cancel']):
            # User wants fresh start - clear order state
            new_order_state = OrderState()
            new_order_state.order_status = "new"

            self.conversation_manager.update_order_state(
                self.current_conversation_id,
                new_order_state
            )

            return "Baik, kita mulai pesanan baru. Produk apa yang ingin Anda pesan?"

        # User response unclear - ask again
        else:
            return """Maaf, saya kurang mengerti.

    Apakah Anda ingin melanjutkan pesanan sebelumnya?
    Ketik "Ya" untuk melanjutkan atau "Mulai Baru" untuk pesanan baru."""

    def _validate_delivery_date(self, delivery_date: str) -> str:
        """
        Validate delivery date

        Args:
            delivery_date: Date string in YYYY-MM-DD format

        Returns:
            Error message if invalid, None if valid
        """
        from datetime import datetime
        import pytz

        # Parse delivery date
        try:
            delivery_dt = datetime.strptime(delivery_date, "%Y-%m-%d")
        except ValueError:
            return "Maaf, format tanggal tidak valid. Mohon berikan tanggal dalam format yang jelas (contoh: 'besok', '15 Februari', dll)."

        # Get current date in WIB timezone
        wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(wib)
        today = now_wib.date()

        # Convert delivery_dt to date for comparison
        delivery_date_obj = delivery_dt.date()

        # Check 1: Date is in the past
        if delivery_date_obj < today:
            # Calculate how many days ago
            days_ago = (today - delivery_date_obj).days

            if days_ago == 1:
                time_desc = "kemarin"
            elif days_ago == 2:
                time_desc = "kemarin lusa"
            else:
                time_desc = f"{days_ago} hari yang lalu"

            return f"Maaf, tanggal {delivery_date} itu sudah lewat ({time_desc}). Untuk tanggal berapa ya pengirimannya?"

        # Check 2: Date is Sunday (weekday 6)
        if delivery_dt.weekday() == 6:  # Sunday = 6
            # Format date in Indonesian
            day_name = "Minggu"
            date_formatted = delivery_dt.strftime("%d %B %Y")

            # Map month names to Indonesian
            month_map = {
                "January": "Januari", "February": "Februari", "March": "Maret",
                "April": "April", "May": "Mei", "June": "Juni",
                "July": "Juli", "August": "Agustus", "September": "September",
                "October": "Oktober", "November": "November", "December": "Desember"
            }
            for eng, ind in month_map.items():
                date_formatted = date_formatted.replace(eng, ind)

            return f"Maaf, tanggal {date_formatted} itu hari {day_name}. Kami tidak melayani pengiriman di hari Minggu. Bisa pilih tanggal lain?"

        # Valid date
        return None

    # HELPER -- do not change
    def warm_up_cache(self):
        """Load all parts into cache for fast semantic search"""
        print("Warming up cache with customer data...")
        db = SessionLocal()
        parts = db.query(Parts).all()
        for c in parts:
            cache_store.set(c.id, {"id": c.id, "partnum": c.partnum, "description": c.description, "uom": c.uom, "uomdesc": c.uomdesc, "embedding": c.embedding})
        db.close()
        print(f"Cache ready with {len(parts)} records.")
        pass

    def debug_cache(self):
        """Debug: Print cache contents"""
        print("\n" + "="*50)
        print("🔍 CACHE CONTENTS")
        print("="*50)

        # Show all cache keys (convert to string for safety)
        all_keys = list(self.cache_service._cache.keys())
        print(f"\n📋 Total keys in cache: {len(all_keys)}")

        # Group by type (handle both int and string keys)
        order_states = [k for k in all_keys if isinstance(k, str) and k.startswith("order_state:")]
        contexts = [k for k in all_keys if isinstance(k, str) and k.startswith("context:")]
        customers = [k for k in all_keys if isinstance(k, str) and k.startswith("customer:")]
        products = [k for k in all_keys if isinstance(k, int)]  # Product IDs are integers

        print(f"\n📦 Products cached: {len(products)}")
        print(f"💬 Conversations cached: {len(contexts)}")
        print(f"📝 Order states cached: {len(order_states)}")
        print(f"👤 Customers cached: {len(customers)}")

        # Show current conversation
        if self.current_conversation_id:
            print(f"\n🎯 CURRENT CONVERSATION: {self.current_conversation_id}")

            # Show order state
            order_state_key = f"order_state:{self.current_conversation_id}"
            if order_state_key in self.cache_service._cache:
                print(f"\n📝 Order State:")
                import json
                print(json.dumps(self.cache_service._cache[order_state_key], indent=2, ensure_ascii=False))

            # Show context
            context_key = f"context:{self.current_conversation_id}"
            if context_key in self.cache_service._cache:
                print(f"\n💬 Conversation Context (last {len(self.cache_service._cache[context_key])} messages):")
                for msg in self.cache_service._cache[context_key]:
                    print(f"  {msg['role']:10s}: {msg['content'][:60]}...")

        print("="*50 + "\n")


