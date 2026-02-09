"""
SIMPLE CONNECTION TEST - Start Fresh
"""

print("=" * 70)
print("🔍 STEP-BY-STEP DATABASE CONNECTION TEST")
print("=" * 70)
print()

# Step 1: Test import
print("Step 1: Testing imports...")
try:
    import psycopg2
    print("✅ psycopg2 installed")
except ImportError:
    print("❌ psycopg2 NOT installed")
    print("   Run: pip install psycopg2-binary")
    exit(1)

try:
    from sqlalchemy import create_engine, text
    print("✅ sqlalchemy installed")
except ImportError:
    print("❌ sqlalchemy NOT installed")
    print("   Run: pip install sqlalchemy")
    exit(1)

print()

# Step 2: Test different configurations
print("Step 2: Testing PostgreSQL connections...")
print()

configs = [
    {"host": "localhost", "port": 5432, "user": "postgres", "password": "admin", "database": "postgres"},
    {"host": "localhost", "port": 5431, "user": "postgres", "password": "admin", "database": "postgres"},
    {"host": "localhost", "port": 5432, "user": "postgres", "password": "password", "database": "postgres"},
    {"host": "localhost", "port": 5432, "user": "postgres", "password": "postgres", "database": "postgres"},
]

working_config = None

for i, config in enumerate(configs, 1):
    print(f"Test {i}: host={config['host']}, port={config['port']}, password='{config['password']}'")
    
    try:
        # Test with psycopg2 directly (faster)
        conn = psycopg2.connect(
            host=config['host'],
            port=config['port'],
            user=config['user'],
            password=config['password'],
            database=config['database'],
            connect_timeout=3
        )
        conn.close()
        
        print(f"   ✅ SUCCESS! PostgreSQL found!")
        working_config = config
        break
        
    except psycopg2.OperationalError as e:
        error_msg = str(e).split('\n')[0]
        print(f"   ❌ Failed: {error_msg}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")

print()
print("=" * 70)

if working_config:
    print("✅ POSTGRESQL CONNECTION SUCCESSFUL!")
    print("=" * 70)
    print()
    print("Working Configuration:")
    print(f"  Host:     {working_config['host']}")
    print(f"  Port:     {working_config['port']}")
    print(f"  User:     {working_config['user']}")
    print(f"  Password: {working_config['password']}")
    print()
    
    # Step 3: Check if chatbot_samator database exists
    print("Step 3: Checking for 'chatbot_samator' database...")
    
    try:
        conn = psycopg2.connect(
            host=working_config['host'],
            port=working_config['port'],
            user=working_config['user'],
            password=working_config['password'],
            database='chatbot_samator',
            connect_timeout=3
        )
        conn.close()
        
        print("✅ Database 'chatbot_samator' EXISTS!")
        print()
        print("=" * 70)
        print("✅ ALL CHECKS PASSED - DATABASE IS READY!")
        print("=" * 70)
        print()
        print("Your .env should be:")
        print(f"DATABASE_URL=postgresql://{working_config['user']}:{working_config['password']}@{working_config['host']}:{working_config['port']}/chatbot_samator")
        print()
        print("Next step: Run 'python setup_database.py'")
        
    except psycopg2.OperationalError as e:
        if 'does not exist' in str(e):
            print("❌ Database 'chatbot_samator' DOES NOT EXIST")
            print()
            print("=" * 70)
            print("⚠️  YOU NEED TO CREATE THE DATABASE FIRST")
            print("=" * 70)
            print()
            print("Option 1: Using pgAdmin (Recommended)")
            print("  1. Open pgAdmin 4")
            print("  2. Connect to PostgreSQL server")
            print(f"     (password: {working_config['password']})")
            print("  3. Right-click 'Databases' → Create → Database")
            print("  4. Name: chatbot_samator")
            print("  5. Click Save")
            print()
            print("Option 2: Using Python")
            print("  Run this command:")
            print()
            print(f"  python -c \"import psycopg2; conn = psycopg2.connect(host='{working_config['host']}', port={working_config['port']}, user='{working_config['user']}', password='{working_config['password']}', database='postgres'); conn.autocommit = True; cur = conn.cursor(); cur.execute('CREATE DATABASE chatbot_samator'); cur.close(); conn.close(); print('✅ Database created!')\"")
            print()
            print("After creating database, update your .env:")
            print(f"DATABASE_URL=postgresql://{working_config['user']}:{working_config['password']}@{working_config['host']}:{working_config['port']}/chatbot_samator")
        else:
            print(f"❌ Error: {e}")
            
else:
    print("❌ COULD NOT CONNECT TO POSTGRESQL")
    print("=" * 70)
    print()
    print("Troubleshooting:")
    print()
    print("1. Is PostgreSQL running?")
    print("   Check: Get-Service -Name postgresql*")
    print("   Should show: Running")
    print()
    print("2. What is your PostgreSQL password?")
    print("   - Try opening pgAdmin 4")
    print("   - Note the password you use to connect")
    print()
    print("3. What port is PostgreSQL using?")
    print("   - In pgAdmin, right-click server → Properties → Connection")
    print("   - Check the Port number")
    print()
    print("4. After finding correct settings, update .env file")

print()
print("=" * 70)

