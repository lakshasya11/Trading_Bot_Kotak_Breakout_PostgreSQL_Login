import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME_TODAY = os.getenv("DB_NAME_TODAY", "trading_kotak_today")

DATABASE_URL_TODAY = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME_TODAY}"

def check_latest_trades():
    print(f"\n--- Checking Latest Trades in: {DB_NAME_TODAY} ---")
    try:
        engine = create_engine(DATABASE_URL_TODAY)
        with engine.connect() as conn:
            # Check latest 10 trades regardless of time
            query = text("""
                SELECT id, timestamp, symbol, pnl, user_name, ucc, direction, exit_mode
                FROM trades 
                ORDER BY id DESC
                LIMIT 10
            """)
            df = pd.read_sql_query(query, conn)
            if df.empty:
                print("No trades found in database.")
            else:
                print(f"Latest {len(df)} trades found:")
                print(df)
                
            # Check if any have been added in the last 10 minutes
            from datetime import datetime, timedelta
            ten_min_ago = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\nChecking for trades since {ten_min_ago}...")
            
            recent_query = text("SELECT COUNT(*) FROM trades WHERE timestamp > :time")
            recent_count = conn.execute(recent_query, {"time": ten_min_ago}).fetchone()[0]
            print(f"Trades in last 10 minutes: {recent_count}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_latest_trades()
