import os

def create_structure():
    # Define the folder structure based on your architecture
    structure = [
        "src/agents",
        "src/core",
        "src/validators",
        "src/services",
        "src/models",
        "src/database",
        "src/cli",
        "src/utils",
        "src/config/prompts",
        "tests/test_validators",
        "data/vector_store",
        "logs",
        "notebooks"
    ]

    # Define the initial files to create
    files = [
        "src/main.py",
        "src/agents/base_agent.py",
        "src/agents/order_agent.py",
        "src/core/intent_classifier.py",
        "src/core/orchestrator.py",
        "src/core/entity_extractor.py",
        "src/core/state_manager.py",
        "src/core/dialog_manager.py",
        "src/validators/format_validator.py",
        "src/validators/product_validator.py",
        "src/validators/customer_validator.py",
        "src/validators/uom_validator.py",
        "src/validators/date_validator.py",
        "src/services/llm_service.py",
        "src/services/vector_db_service.py",
        "src/services/sql_service.py",
        "src/services/cache_service.py",
        "src/models/order_state.py",
        "src/models/entities.py",
        "src/models/validation_result.py",
        "src/models/conversation.py",
        "src/database/sql_schema.py",
        "src/database/vector_db_setup.py",
        "src/database/seed_data.py",
        "src/cli/chat_interface.py",
        "src/cli/formatter.py",
        "src/cli/input_handler.py",
        "src/config/settings.py",
        "src/config/prompts/extraction_prompt.py",
        "src/config/prompts/validation_prompt.py",
        "src/config/prompts/dialog_prompts.py",
        ".env",
        "requirements.txt",
        "README.md"
    ]

    print("🏗️ Creating project structure...")

    # Create directories
    for folder in structure:
        os.makedirs(folder, exist_ok=True)
        # Create __init__.py in every python package folder
        if "src" in folder or "tests" in folder:
            init_file = os.path.join(folder, "__init__.py")
            with open(init_file, "a"): pass 

    # Create empty files
    for file_path in files:
        # Create directory for the file if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                if file_path.endswith(".py"):
                    f.write(f"# {os.path.basename(file_path)}\n")
            print(f"  ✅ Created: {file_path}")

    print("\n🚀 Project setup complete!")

if __name__ == "__main__":
    create_structure()