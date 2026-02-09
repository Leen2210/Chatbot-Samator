"""
Test Semantic Search Service
"""

from src.services.semantic_search_service import semantic_search_service

def test_fuzzy_search():
    """Test fuzzy text search (fallback when embeddings not available)"""
    print("="*70)
    print("🔍 TEST FUZZY SEARCH (Text-based)")
    print("="*70)
    
    test_queries = [
        "oksigen",
        "nitrogen",
        "argon",
        "helium",
        "liquid n2",
        "gas oksigen"
    ]
    
    for query in test_queries:
        print(f"\n📝 Query: '{query}'")
        print("-" * 70)
        
        results = semantic_search_service.fuzzy_search_by_description(query, top_k=3)
        
        if results:
            for i, result in enumerate(results, 1):
                print(f"  {i}. {result['description']}")
                print(f"     Part Number: {result['partnum']}")
                print(f"     UOM: {result['uom']} ({result['uomdesc']})")
        else:
            print("  ❌ No matches found")
    
    print("\n" + "="*70)

def test_exact_partnum():
    """Test exact part number search"""
    print("\n" + "="*70)
    print("🔍 TEST EXACT PART NUMBER SEARCH")
    print("="*70)
    
    test_partnums = [
        "BULIN0010000000",
        "BULAR0010000000",
        "BULHE0010000000",
        "INVALID_PARTNUM"
    ]
    
    for partnum in test_partnums:
        print(f"\n📝 Part Number: '{partnum}'")
        print("-" * 70)
        
        result = semantic_search_service.search_by_partnum(partnum)
        
        if result:
            print(f"  ✅ Found: {result['description']}")
            print(f"     UOM: {result['uom']} ({result['uomdesc']})")
        else:
            print("  ❌ Not found")
    
    print("\n" + "="*70)

def test_semantic_search():
    """Test semantic search with embeddings"""
    print("\n" + "="*70)
    print("🔍 TEST SEMANTIC SEARCH (Embedding-based)")
    print("="*70)
    print("⚠️  NOTE: This requires proper embedding model to work well")
    print("="*70)
    
    test_queries = [
        "oksigen cair",
        "nitrogen liquid",
        "gas argon"
    ]
    
    for query in test_queries:
        print(f"\n📝 Query: '{query}'")
        print("-" * 70)
        
        results = semantic_search_service.search_part_by_description(query, top_k=3, threshold=0.3)
        
        if results:
            for i, result in enumerate(results, 1):
                similarity_pct = int(result['similarity'] * 100)
                print(f"  {i}. {result['description']} (similarity: {similarity_pct}%)")
                print(f"     Part Number: {result['partnum']}")
                print(f"     UOM: {result['uom']}")
        else:
            print("  ⚠️  No semantic matches (falling back to fuzzy search)")
            fuzzy_results = semantic_search_service.fuzzy_search_by_description(query, top_k=3)
            if fuzzy_results:
                for i, result in enumerate(fuzzy_results, 1):
                    print(f"  {i}. {result['description']}")
                    print(f"     Part Number: {result['partnum']}")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    print("\n🚀 TESTING SEMANTIC SEARCH SERVICE\n")
    
    # Test 1: Fuzzy search (always works)
    test_fuzzy_search()
    
    # Test 2: Exact part number
    test_exact_partnum()
    
    # Test 3: Semantic search (needs proper embedding model)
    test_semantic_search()
    
    print("\n✅ ALL TESTS COMPLETED\n")

