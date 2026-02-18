# main.py
import sys
import os

# Ensure the root directory is in the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.sql_service import init_db
from src.core.orchestrator import Orchestrator
from src.services.cache_service import CacheService
from src.services.sql_service import SQLService

def start_terminal_chat():
    print("--- INITIALIZING ORDER BOT ---")
    
    # Initialize Postgres Tables
    try:
        init_db()
        print("[1/2] Database connection successful.")
    except Exception as e:
        print(f"FAILED to connect to Database: {e}")
        return
    
    # Initialize Brain
    orchestrator = Orchestrator()

    # Simulate phone number (in real WhatsApp integration, this comes from webhook)
    phone_number = "+00000000001"
    conversation_id, welcome_message = orchestrator.start_conversation(phone_number)
    
    print(f"Conversation ID: {conversation_id}")

    print("[2/2] Orchestrator ready.")
    print("\n--- CHAT STARTED (Type 'exit' to quit) ---")
    print()
    print("bot: ", welcome_message)
    while True:
        try:
            user_text = input("\nYou: ").strip()
            
            if not user_text:
                continue

            # 🔍 Debug command
            if user_text.lower() == "debug":
                orchestrator.debug_cache()
                continue
                
            if user_text.lower() in ["exit", "quit", "bye"]:
                print("Bot: Goodbye! Have a great day.")
                break

            response = orchestrator.handle_message(user_text)
            print(f"Bot: {response}")

        except KeyboardInterrupt:
            print("\nBot: Session ended.")
            break

if __name__ == "__main__":
    start_terminal_chat()