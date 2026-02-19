# src/services/date_calculator_service.py
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import Optional, Dict

class DateCalculatorService:
    """Calculate dates from temporal JSON schema"""
    
    def calculate_date(self, date_raw: Dict) -> Optional[str]:
        """
        Calculate YYYY-MM-DD from temporal JSON
        
        Args:
            date_raw: {"day_offset": 1} or {"target_weekday": "senin", "extra_weeks": 1}
        
        Returns:
            "YYYY-MM-DD" or None
        """
        if not date_raw:
            return None
        
        today = datetime.now()
        future_date = datetime(today.year, today.month, today.day)
        
        # A. Handle Bulan & Tanggal Spesifik
        extra_months = date_raw.get("extra_months", 0)
        target_date = date_raw.get("target_date")
        
        if extra_months > 0 or target_date:
            future_date += relativedelta(months=extra_months)
            if target_date:
                future_date += relativedelta(day=target_date)
                return future_date.strftime("%Y-%m-%d")
        
        # B. Handle Hari dalam Seminggu (Senin-Minggu)
        target_weekday_str = date_raw.get("target_weekday")
        extra_weeks = date_raw.get("extra_weeks", 0)
        
        if target_weekday_str:
            days_map = {
                "senin": 0, "selasa": 1, "rabu": 2, "kamis": 3,
                "jumat": 4, "sabtu": 5, "minggu": 6
            }
            target_weekday = days_map.get(target_weekday_str.lower())
            
            if target_weekday is not None:
                # Cari Senin minggu ini
                current_week_monday = future_date - timedelta(days=future_date.weekday())
                
                # Hitung tanggal target
                calculated_date = current_week_monday + timedelta(days=target_weekday) + timedelta(weeks=extra_weeks)
                
                # Prevent past dates
                if calculated_date.date() < today.date():
                    calculated_date += timedelta(weeks=1)
                
                return calculated_date.strftime("%Y-%m-%d")
        
        # Handle extra weeks tanpa nama hari
        elif extra_weeks > 0:
            future_date += timedelta(weeks=extra_weeks)
            return future_date.strftime("%Y-%m-%d")
        
        # C. Handle Penambahan Hari Langsung (Besok, Lusa)
        day_offset = date_raw.get("day_offset", 0)
        if day_offset > 0:
            future_date += timedelta(days=day_offset)
        
        return future_date.strftime("%Y-%m-%d")

# Singleton
date_calculator_service = DateCalculatorService()
