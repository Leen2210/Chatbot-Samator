"""
Standalone Interactive Semantic Search Tester
Purpose: Test BGE-M3 embeddings and similarity scores for products.
"""
import sys
import os

# Ensure the script can find the 'src' directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.services.semantic_search_service import semantic_search_service

def run_interactive_test():
    print("\n" + "="*70)
    print("🚀 SAMATOR PRODUCT SEMANTIC SEARCH TESTER")
    print("Model: BAAI/bge-m3 (Multilingual)")
    print("="*70)
    print("Instructions:")
    print("- Type a product description (e.g., 'oksigen cair', 'nitrogen liquid')")
    print("- Type 'exit' to quit")
    print("-" * 70)

    while True:
        query = input("\n🔍 Enter search query: ").strip()
        
        if query.lower() in ['exit', 'quit', 'q']:
            print("Exiting...")
            break
        
        if not query:
            continue

        print(f"Searching for: '{query}'...")
        
        # We use a lower threshold (0.3) to see more potential matches during testing
        results = semantic_search_service.search_part_by_description(
            query, 
            top_k=5, 
            threshold=0.55
        )

        if results:
            print(f"\n✅ Found {len(results)} matches:")
            print(f"{'No.':<4} {'Similarity':<12} {'Part Number':<18} {'Description'}")
            print("-" * 70)
            for i, res in enumerate(results, 1):
                sim_pct = f"{res['similarity']*100:.1f}%"
                print(f"{i:<4} {sim_pct:<12} {res['partnum']:<18} {res['description']}")
                print(f"     UOM: {res['uom']} ({res['uomdesc']})")
        else:
            print("\n⚠️  No semantic matches found above threshold (0.3).")
            print("Attempting fuzzy fallback...")
            
            fuzzy_results = semantic_search_service.fuzzy_search_by_description(query, top_k=3)
            if fuzzy_results:
                print(f"Found {len(fuzzy_results)} fuzzy matches:")
                for i, res in enumerate(fuzzy_results, 1):
                    print(f"  {i}. [{res['partnum']}] {res['description']}")
            else:
                print("❌ No matches found even with fuzzy search.")

if __name__ == "__main__":
    try:
        run_interactive_test()
    except KeyboardInterrupt:
        print("\nTest aborted by user.")
    except Exception as e:
        print(f"\n❌ Error: {e}")