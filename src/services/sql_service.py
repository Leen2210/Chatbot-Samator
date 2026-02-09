# sql_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config.settings import settings
from src.database.sql_schema import Base, Customer, Parts, Order
from src.services.cache_service import cache_store
# 1. Create Engine
engine = create_engine(settings.DATABASE_URL)

# 2. Create Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Creates tables defined in sql_schema.py"""
    Base.metadata.create_all(bind=engine)

class SQLService:
    def __init__(self):
        self.db = SessionLocal()

    def close(self):
        self.db.close()

    def get_customer(self, customer_id: str):
        # 1. Check Cache first
        cached_data = cache_store.get(customer_id)
        if cached_data:
            print(f"DEBUG: Cache Hit for {customer_id}")
            return cached_data

        # 2. Cache Miss -> Check Postgres
        print(f"DEBUG: Cache Miss for {customer_id}. Querying DB...")
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()

        # 3. If found, save to Cache for next time
        if customer:
            # We convert to a dict or object that's easy to store
            customer_data = {
                "id": customer.id,
                "customername": customer.customername,
                "customermainphone": customer.customermainphone
            }
            cache_store.set(customer_id, customer_data)
            return customer_data
        
        return None
    
    def get_part(self, part_num: str):
        # 1. Check Cache first
        cached_data = cache_store.get(part_num)
        if cached_data:
            print(f"DEBUG: Cache Hit for {part_num}")
            return cached_data

        # 2. Cache Miss -> Check Postgres
        print(f"DEBUG: Cache Miss for {part_num}. Querying DB...")
        part = self.db.query(Parts).filter(Parts.partnum == part_num).first()

        # 3. If found, save to Cache for next time
        if part:
            # We convert to a dict or object that's easy to store
            part_data = {
                "id": part.id,
                "partnum": part.partnum,
                "description": part.description,
                "uom": part.uom,
                "uomdesc": part.uomdesc,
                "embedding": part.embedding  # This will be a Python list
            }
            cache_store.set(part_num, part_data)
            return part_data
        
        return None
    
sql_service = SQLService()
