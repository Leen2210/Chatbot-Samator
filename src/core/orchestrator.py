# src/core/orchestrator.py
"""
Orchestrator — the central nervous system. A pure router.

Responsibilities (ONLY these):
  1. Manage conversation session (start/resume)
  2. Detect language
  3. Run intent classification on EVERY message
  4. Route message to the correct Agent
  5. Persist messages via ConversationManager
  6. Hold session-level flags (awaiting_resume, awaiting_confirmation)

The Orchestrator does NOT:
  - Build LLM prompts
  - Validate dates
  - Run semantic search
  - Write Orders to the database
  - Know anything about order field logic
  - Handle completed order responses (that's OrderAgent's job)
"""

from src.services.cache_service import cache_store
from src.services.sql_service import SessionLocal
from src.core.conversation_manager import conversation_manager
from src.core.intent_classifier import intent_classifier
from src.agents.order_agent import order_agent
from src.agents.chit_chat_agent import chit_chat_agent
from src.agents.escalation_agent import escalation_agent
from src.utils.language_detector import language_detector
from src.database.sql_schema import Parts


class Orchestrator:
    def __init__(self):
        self.current_conversation_id: str | None = None
        self.current_language: str = "id"
        self.awaiting_resume_response: bool = False
        self.awaiting_order_confirmation: bool = False

        self._warm_up_cache()

    # ------------------------------------------------------------------ #
    #  Session start                                                       #
    # ------------------------------------------------------------------ #

    def start_conversation(self, phone_number: str) -> tuple[str, str]:
        """
        Initialize or resume a conversation.
        Returns (conversation_id, welcome_message).
        """
        conversation_id, order_status, last_order_state = \
            conversation_manager.get_or_create_conversation(phone_number)

        self.current_conversation_id = conversation_id

        # Incomplete order → offer to resume
        if order_status == "in_progress":
            welcome_message = order_agent.generate_resume_prompt(last_order_state)
            conversation_manager.add_message(conversation_id, "assistant", welcome_message)
            self.awaiting_resume_response = True
            return conversation_id, welcome_message

        # New or returning user
        context = conversation_manager.get_context(conversation_id)
        if not context:
            welcome_message = (
                "Halo! Saya Chatbot Asisten mu hari ini! "
                "Ada yang bisa saya bantu dengan pemesanan produk?"
            )
        else:
            welcome_message = "Selamat datang kembali! Ada yang bisa saya bantu hari ini?"

        conversation_manager.add_message(conversation_id, "assistant", welcome_message)
        self.awaiting_resume_response = False
        return conversation_id, welcome_message

    # ------------------------------------------------------------------ #
    #  Message routing — the ONLY job of this class                        #
    # ------------------------------------------------------------------ #

    def handle_message(self, user_message: str) -> str:
        """
        Route an incoming message to the correct agent and return the response.

        Every message passes through here. Intent is always classified.
        State flags (awaiting_resume, awaiting_confirmation) act as
        priority overrides BEFORE the intent routing table.
        """
        self._update_language(user_message)

        context = conversation_manager.get_context(self.current_conversation_id)
        order_state = conversation_manager.get_order_state(self.current_conversation_id)

        # ── PRIORITY 1: Resume flow ──────────────────────────────────── #
        # The user was asked whether to resume an incomplete order.
        # We skip intent classification here — any response goes to OrderAgent.
        if self.awaiting_resume_response:
            response = order_agent.handle_resume_response(
                user_message, self.current_conversation_id, self.current_language
            )
            self.awaiting_resume_response = False
            conversation_manager.add_message(self.current_conversation_id, "user", user_message)
            conversation_manager.add_message(self.current_conversation_id, "assistant", response)
            return response

        # ── PRIORITY 2: Confirmation awaiting ────────────────────────── #
        # When awaiting_order_confirmation is True, the user is in a bounded
        # state: they can only confirm, cancel, or edit. ANY message at this
        # point — including "ya" (classified as CHIT_CHAT by the LLM) — must
        # go directly to OrderAgent.
        #
        # We intentionally skip intent classification here because "ya" in
        # isolation looks like a courtesy word, not a confirmation. The flag
        # gives us context the classifier does not have.
        if self.awaiting_order_confirmation:
            conversation_manager.add_message(
                self.current_conversation_id, "user", user_message
            )
            response = order_agent.handle(
                user_message,
                self.current_conversation_id,
                order_state,
                context,
                intent_result=None,          # not needed — agent checks flag
                language=self.current_language,
                awaiting_confirmation=True,
            )
            self.awaiting_order_confirmation = getattr(
                order_agent, "_last_confirmation_flag", False
            )
            conversation_manager.add_message(
                self.current_conversation_id, "assistant", response
            )
            return response

        # ── ALWAYS: Classify intent ──────────────────────────────────── #
        # Runs on every normal message. This is what enables the escape hatch:
        #   - Mid-order price question → FALLBACK → EscalationAgent
        #   - Mid-order "batal"        → CANCEL_ORDER → OrderAgent
        # OrderState in DB is untouched during FALLBACK, so ordering resumes cleanly.
        intent_result = intent_classifier.classify_and_extract(
            user_message, order_state, history=context[-4:]
        )
        print(f"[Orchestrator] Intent: {intent_result.intent}")

        # Store user message with extracted entities
        conversation_manager.add_message(
            self.current_conversation_id,
            "user",
            user_message,
            entities=intent_result.entities.model_dump(),
        )

        # ── Routing table ────────────────────────────────────────────── #
        if intent_result.intent == "CHIT_CHAT":
            response = chit_chat_agent.handle(
                user_message,
                self.current_conversation_id,
                order_state,
                context,
                language=self.current_language,
            )

        elif intent_result.intent in ("ORDER", "CANCEL_ORDER"):
            response = order_agent.handle(
                user_message,
                self.current_conversation_id,
                order_state,
                context,
                intent_result=intent_result,
                language=self.current_language,
                awaiting_confirmation=False,  # flag was False to reach here
            )
            self.awaiting_order_confirmation = getattr(
                order_agent, "_last_confirmation_flag", False
            )

        else:
            # FALLBACK or UNKNOWN → escalate to call center.
            # OrderState is preserved in DB/cache; ordering resumes on next ORDER message.
            response = escalation_agent.handle(
                user_message,
                self.current_conversation_id,
                order_state,
                context,
                language=self.current_language,
            )

        conversation_manager.add_message(
            self.current_conversation_id, "assistant", response
        )
        return response

    # ------------------------------------------------------------------ #
    #  Language detection                                                  #
    # ------------------------------------------------------------------ #

    def _update_language(self, user_message: str):
        """
        Lock language after first detection.
        Only switch if user explicitly requests it.
        """
        user_lower = user_message.lower()

        en_triggers = ["speak english", "talk in english", "use english", "english please"]
        id_triggers = ["bahasa indonesia", "pakai bahasa indonesia", "bicara bahasa indonesia"]

        if any(t in user_lower for t in en_triggers):
            self.current_language = "en"
        elif any(t in user_lower for t in id_triggers):
            self.current_language = "id"
        else:
            # On the very first message, detect the language and lock it.
            # After that, keep it locked to prevent accidental switches
            # (e.g., user types an English product name mid-Indonesian conversation).
            if not self.current_conversation_id:
                self.current_language = language_detector.detect(user_message)

    # ------------------------------------------------------------------ #
    #  Debug / utility                                                     #
    # ------------------------------------------------------------------ #

    def get_current_order_state(self) -> dict:
        if not self.current_conversation_id:
            return {}
        return conversation_manager.get_order_state(self.current_conversation_id).to_dict()

    def debug_cache(self):
        print("\n" + "=" * 50)
        print("🔍 CACHE CONTENTS")
        print("=" * 50)
        all_keys = list(cache_store._cache.keys())
        order_states = [k for k in all_keys if isinstance(k, str) and k.startswith("order_state:")]
        contexts = [k for k in all_keys if isinstance(k, str) and k.startswith("context:")]
        products = [k for k in all_keys if isinstance(k, int)]
        print(f"📦 Products cached : {len(products)}")
        print(f"💬 Contexts cached : {len(contexts)}")
        print(f"📝 Order states    : {len(order_states)}")
        if self.current_conversation_id:
            print(f"\n🎯 Current conversation: {self.current_conversation_id}")
            key = f"order_state:{self.current_conversation_id}"
            if key in cache_store._cache:
                import json
                print(json.dumps(cache_store._cache[key], indent=2, ensure_ascii=False))
        print("=" * 50 + "\n")

    def _warm_up_cache(self):
        print("Warming up cache with parts data...")
        db = SessionLocal()
        parts = db.query(Parts).all()
        for p in parts:
            cache_store.set(p.id, {
                "id": p.id, "partnum": p.partnum, "description": p.description,
                "uom": p.uom, "uomdesc": p.uomdesc, "embedding": p.embedding,
            })
        db.close()
        print(f"Cache ready with {len(parts)} records.")