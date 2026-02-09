"""
AUTO SETUP DATABASE - Create tables and import CSV data
"""

import os
import csv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.database.sql_schema import Base, Customer, Parts
from src.config.settings import settings

# Colors for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{'='*60}")
    print(f"{Colors.BOLD}{text}{Colors.RESET}")
    print(f"{'='*60}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")

def test_connection():
    """Test database connection"""
    print_header("STEP 1: Testing Database Connection")
    
    try:
        engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"connect_timeout": 5},
            pool_pre_ping=True
        )
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();"))
            version = result.fetchone()[0].split(',')[0]
            print_success("Connected to PostgreSQL")
            print_info(f"Version: {version}")
            return engine
            
    except Exception as e:
        print_error(f"Connection failed: {e}")
        return None

def create_tables(engine):
    """Create all tables"""
    print_header("STEP 2: Creating Tables")
    
    try:
        # Create all tables
        Base.metadata.create_all(bind=engine)
        
        # Check tables
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tables = [row[0] for row in result]
            
            if tables:
                print_success(f"Database ready with {len(tables)} tables:")
                for table in tables:
                    print(f"   ✓ {table}")
            else:
                print_info("No existing tables found")
                print_success("Tables created successfully")
        
        return True
        
    except Exception as e:
        print_error(f"Failed to create tables: {e}")
        return False

def import_customers(session, filename):
    """Import customers from CSV"""
    print_header("STEP 3: Importing Customers")
    
    if not os.path.exists(filename):
        print_error(f"File not found: {filename}")
        return False
    
    print_info(f"Reading from: {filename}")
    
    try:
        count = 0
        skipped = 0
        errors = 0
        
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            
            for row in reader:
                try:
                    customer_id = row['id'].strip()
                    
                    # Check if exists
                    existing = session.query(Customer).filter_by(id=customer_id).first()
                    if existing:
                        skipped += 1
                        continue
                    
                    # Create new
                    customer = Customer(
                        id=customer_id,
                        customername=row['customername'].strip(),
                        customermainphone=row['customermainphone'].strip()
                    )
                    session.add(customer)
                    count += 1
                    
                    # Commit every 1000
                    if count % 1000 == 0:
                        session.commit()
                        print(f"   📥 Imported {count:,} customers...")
                        
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print_warning(f"Row error: {e}")
                    continue
            
            session.commit()
            print_success(f"Imported {count:,} customers")
            if skipped > 0:
                print_info(f"Skipped {skipped:,} existing customers")
            if errors > 0:
                print_warning(f"Errors: {errors}")
            return True
            
    except Exception as e:
        print_error(f"Import failed: {e}")
        session.rollback()
        return False

def import_parts(session, filename):
    """Import parts from CSV"""
    print_header("STEP 4: Importing Parts")
    
    if not os.path.exists(filename):
        print_error(f"File not found: {filename}")
        return False
    
    print_info(f"Reading from: {filename}")
    
    try:
        count = 0
        skipped = 0
        errors = 0

        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')

            for row in reader:
                try:
                    part_id = int(row['id'].strip())

                    # Check if exists
                    existing = session.query(Parts).filter_by(id=part_id).first()
                    if existing:
                        skipped += 1
                        continue

                    # Parse embedding
                    embedding_str = row['embedding'].strip().strip('{}')
                    embedding = [float(x.strip()) for x in embedding_str.split(',')]

                    # Create new
                    part = Parts(
                        id=part_id,
                        partnum=row['partnum'].strip(),
                        description=row['description'].strip(),
                        uom=row['uom'].strip(),
                        uomdesc=row['uomdesc'].strip(),
                        embedding=embedding
                    )
                    session.add(part)
                    count += 1

                    # Commit every 100
                    if count % 100 == 0:
                        session.commit()
                        print(f"   📥 Imported {count:,} parts...")

                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        print_warning(f"Row error: {e}")
                    continue

            session.commit()
            print_success(f"Imported {count:,} parts")
            if skipped > 0:
                print_info(f"Skipped {skipped:,} existing parts")
            if errors > 0:
                print_warning(f"Errors: {errors}")
            return True

    except Exception as e:
        print_error(f"Import failed: {e}")
        session.rollback()
        return False

def show_summary(engine):
    """Show database summary"""
    print_header("STEP 5: Database Summary")

    try:
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()

        total_customers = session.query(Customer).count()
        total_parts = session.query(Parts).count()

        print_success("Database is ready!")
        print(f"\n   👥 Customers: {total_customers:,}")
        print(f"   📦 Parts: {total_parts:,}")

        session.close()
        return True
    except Exception as e:
        print_error(f"Failed to get summary: {e}")
        return False

def main():
    """Main function"""
    print_header("🚀 AUTO SETUP DATABASE - Chatbot Samator")

    # Step 1: Test connection
    engine = test_connection()
    if not engine:
        print_error("Setup failed - cannot connect to database")
        return

    # Step 2: Create tables
    if not create_tables(engine):
        print_error("Setup failed - cannot create tables")
        return

    # Create session
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        # Step 3: Import customers
        import_customers(session, 'table_customers')

        # Step 4: Import parts
        import_parts(session, 'table_parts')

        # Step 5: Show summary
        show_summary(engine)

        print_header("✅ SETUP COMPLETED SUCCESSFULLY!")
        print_info("You can now run: python src/main.py")

    except Exception as e:
        print_error(f"Setup failed: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    main()
