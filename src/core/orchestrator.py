# src/core/orchestrator.py
from src.services.cache_service import cache_store
from src.services.sql_service import sql_service, SessionLocal
from src.services.llm_service import llm_service
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
        """Handle incoming user message"""
        
        # 1. Store user message to DB
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='user',
            content=user_message
        )
        
        # 🆕 2. Handle resume response if waiting for it
        if self.awaiting_resume_response:
            response = self._handle_resume_response(user_message)
            
            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            
            self.awaiting_resume_response = False
            return response
        
        # 3. Get current order state
        current_order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        
        # 🆕 4. Check if order is completed - block modifications
        if current_order_state.order_status == "completed":
            context = self.conversation_manager.get_context(self.current_conversation_id)
            response = self._generate_response(current_order_state, user_message, context)
            
            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            
            return response
        
        # 🆕 5. Handle order confirmation flow
        if self.awaiting_order_confirmation:
            response = self._handle_confirmation_response(user_message, current_order_state)
            
            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            
            # awaiting_order_confirmation flag is cleared inside _handle_confirmation_response
            return response
        
        # 🆕 6. Check if order just became complete - show confirmation prompt
        # Update order state first (in case LLM extracted new info)
        current_order_state.update_missing_fields()
        
        if current_order_state.is_complete and current_order_state.order_status == "in_progress":
            # Order is complete - show confirmation prompt
            response = self._generate_confirmation_prompt(current_order_state)
            self.awaiting_order_confirmation = True  # Set flag
            
            self.conversation_manager.add_message(
                conversation_id=self.current_conversation_id,
                role='assistant',
                content=response
            )
            
            return response
        
        # 7. Normal flow - order still in progress, need more info
        context = self.conversation_manager.get_context(self.current_conversation_id)
        response = self._generate_response(current_order_state, user_message, context)
        
        # 🆕 8. Try to extract entities from user message and update order state
        # (This is where you'd call entity extraction if you add it back later)
        # For now, we rely on LLM to naturally guide the conversation
        
        self.conversation_manager.add_message(
            conversation_id=self.current_conversation_id,
            role='assistant',
            content=response
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
        # Get current order state
        order_state = self.conversation_manager.get_order_state(self.current_conversation_id)
        
        # Validate order is complete
        if not order_state.is_complete:
            return "Pesanan belum lengkap. Mohon lengkapi informasi yang diperlukan."
        
        # TODO: Save order to your orders database/table
        # order_id = self.save_order_to_database(order_state)
        
        # Mark as completed (locks from further edits)
        self.conversation_manager.mark_order_completed(self.current_conversation_id)
        
        # Generate confirmation message
        order_line = order_state.order_lines[0]
        
        confirmation = f"""✅ PESANAN BERHASIL DIKONFIRMASI!

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Nomor Pesanan: [Auto-generated]
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

    def _generate_confirmation_prompt(self, order_state: OrderState) -> str:
        """
        Generate order confirmation prompt when all fields are complete
        
        Args:
            order_state: Complete order state
        
        Returns:
            Confirmation message
        """
        order_line = order_state.order_lines[0]
        
        confirmation = f"""📋 Konfirmasi Pesanan:

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Produk      : {order_line.product_name}
    Jumlah      : {order_line.quantity} {order_line.unit}
    Tanggal     : {order_state.delivery_date}
    Customer    : {order_state.customer_name}
    Perusahaan  : {order_state.customer_company}
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Apakah data sudah benar?

    Ketik:
    - "Ya" / "Konfirmasi" untuk memproses pesanan
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
        
        # 🆕 Option 1: User confirms (Ya/Konfirmasi/OK)
        if any(word in user_input for word in ['ya', 'konfirmasi', 'yes', 'ok', 'oke', 'benar', 'betul']):
            # Complete the order
            response = self.confirm_and_complete_order()
            self.awaiting_order_confirmation = False
            return response
        
        # 🆕 Option 2: User wants to edit (Ubah)
        elif 'ubah' in user_input or 'edit' in user_input or 'ganti' in user_input:
            # Determine what they want to change
            field_to_change = self._detect_field_to_change(user_input)
            
            if field_to_change:
                # Clear that specific field
                self._clear_order_field(order_state, field_to_change)
                
                # Update order state
                order_state.order_status = "in_progress"  # Back to in_progress
                self.conversation_manager.update_order_state(
                    self.current_conversation_id,
                    order_state
                )
                
                self.awaiting_order_confirmation = False
                
                return f"Baik, silakan berikan {field_to_change} yang baru."
            else:
                # Unclear what to change - ask LLM to clarify
                self.awaiting_order_confirmation = False
                return "Baik, field apa yang ingin diubah? (contoh: 'ubah tanggal', 'ubah jumlah')"
        
        # 🆕 Option 3: User cancels (Batal)
        elif any(word in user_input for word in ['batal', 'cancel', 'stop', 'gak jadi', 'tidak jadi']):
            # Cancel order
            order_state.order_status = "cancelled"
            self.conversation_manager.update_order_state(
                self.current_conversation_id,
                order_state
            )
            
            # Mark conversation as completed (cancelled)
            self.conversation_manager.mark_order_complete(self.current_conversation_id)
            
            self.awaiting_order_confirmation = False
            
            return "Pesanan dibatalkan. Terima kasih. Ada yang bisa saya bantu lagi?"
        
        # 🆕 Option 4: Unclear response - ask again
        else:
            return """Maaf, saya kurang mengerti.

    Apakah data pesanan sudah benar?
    Ketik:
    - "Ya" untuk konfirmasi
    - "Ubah [field]" untuk mengubah
    - "Batal" untuk membatalkan"""

    def _detect_field_to_change(self, user_input: str) -> str:
        """
        Detect which field user wants to change
        
        Args:
            user_input: User's message (lowercased)
        
        Returns:
            Field name or None
        """
        if any(word in user_input for word in ['produk', 'product', 'barang']):
            return "produk"
        elif any(word in user_input for word in ['jumlah', 'quantity', 'qty']):
            return "jumlah"
        elif any(word in user_input for word in ['satuan', 'unit', 'tabung', 'botol']):
            return "satuan"
        elif any(word in user_input for word in ['tanggal', 'date', 'kirim', 'delivery']):
            return "tanggal kirim"
        elif any(word in user_input for word in ['nama', 'name', 'customer']):
            return "nama customer"
        elif any(word in user_input for word in ['perusahaan', 'company', 'pt', 'cv']):
            return "nama perusahaan"
        else:
            return None

    def _clear_order_field(self, order_state: OrderState, field: str):
        """
        Clear a specific field in order state
        
        Args:
            order_state: Current order state
            field: Field name to clear
        """
        if field == "produk":
            if len(order_state.order_lines) > 0:
                order_state.order_lines[0].product_name = None
        elif field == "jumlah":
            if len(order_state.order_lines) > 0:
                order_state.order_lines[0].quantity = None
        elif field == "satuan":
            if len(order_state.order_lines) > 0:
                order_state.order_lines[0].unit = None
        elif field == "tanggal kirim":
            order_state.delivery_date = None
        elif field == "nama customer":
            order_state.customer_name = None
        elif field == "nama perusahaan":
            order_state.customer_company = None
        
        # Recalculate missing fields
        order_state.update_missing_fields()

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