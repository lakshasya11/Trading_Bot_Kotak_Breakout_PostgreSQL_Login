import asyncio
import os
from datetime import datetime
from .database import today_engine, all_engine, sql_text

class TradeLogger:
    """Handles all database interactions for logging trades using a connection pool."""
    def __init__(self, db_lock):
        self.db_lock = db_lock
        self.engines = [today_engine, all_engine]

    async def log_trade(self, trade_info):
        """Asynchronously logs a completed trade to the databases using the pool."""
        def db_call():
            columns = ", ".join(trade_info.keys())
            placeholders = ", ".join(f":{key}" for key in trade_info.keys())
            sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
            
            for engine in self.engines:
                try:
                    with engine.begin() as conn:
                        conn.execute(sql_text(sql), trade_info)
                except Exception as e:
                    db_name = engine.url.database
                    print(f"CRITICAL DB ERROR writing to {db_name}: {e}")

        async with self.db_lock:
            await asyncio.to_thread(db_call)

    @staticmethod
    def setup_databases():
        """
        Creates/updates tables if they don't exist and clears the 'today'
        database if it's a new day.
        """
        from .database import BASE_DIR, today_engine, all_engine
        
        create_table_sql = sql_text('''
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                timestamp TEXT NOT NULL,
                trigger_reason TEXT NOT NULL,
                symbol TEXT,
                quantity INTEGER,
                pnl REAL,
                entry_price REAL,
                exit_price REAL,
                exit_reason TEXT,
                trend_state TEXT,
                atr REAL,
                charges REAL,
                net_pnl REAL,
                entry_time TEXT,
                exit_time TEXT,
                duration_seconds REAL,
                max_price REAL,
                signal_time TEXT,
                order_time TEXT,
                expected_entry REAL,
                expected_exit REAL,
                entry_slippage REAL,
                exit_slippage REAL,
                latency_ms INTEGER
            )
        ''')
        
        def upgrade_schema(engine):
            with engine.connect() as conn:
                conn.execute(create_table_sql)
                
                # PostgreSQL schema introspection
                query = sql_text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'trades'
                """)
                cursor = conn.execute(query)
                columns = [row[0] for row in cursor]
                
                # Add missing columns if they don't exist
                new_columns = [
                    ('charges', 'REAL'),
                    ('net_pnl', 'REAL'),
                    ('entry_time', 'TEXT'),
                    ('exit_time', 'TEXT'),
                    ('duration_seconds', 'REAL'),
                    ('max_price', 'REAL'),
                    ('signal_time', 'TEXT'),
                    ('order_time', 'TEXT'),
                    ('expected_entry', 'REAL'),
                    ('expected_exit', 'REAL'),
                    ('entry_slippage', 'REAL'),
                    ('exit_slippage', 'REAL'),
                    ('latency_ms', 'INTEGER'),
                    ('trading_mode', 'TEXT'),
                    ('momentum_price_rising', 'INTEGER'),
                    ('momentum_accelerating', 'INTEGER'),
                    ('momentum_index_sync', 'INTEGER'),
                    ('momentum_volume_surge', 'INTEGER'),
                    ('momentum_checks_passed', 'INTEGER'),
                    ('predictive_order_flow', 'INTEGER'),
                    ('predictive_divergence', 'INTEGER'),
                    ('predictive_structure', 'INTEGER'),
                    ('predictive_checks_passed', 'INTEGER'),
                    ('trigger_system', 'TEXT'),
                    ('entry_type', 'TEXT'),
                    ('supertrend_hold_mode', 'TEXT'),
                    ('entry_option_st_state', 'TEXT'),
                    ('exit_supertrend_reason', 'TEXT'),
                    ('exit_mode', 'TEXT'),
                    ('direction', 'TEXT'),
                    ('candle_open_price', 'REAL'),
                    ('candle_close_price', 'REAL'),
                    ('user_name', 'TEXT')
                ]
                
                for col_name, col_type in new_columns:
                    if col_name not in columns:
                        conn.execute(sql_text(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type};"))
                
                # SQLAlchemy engines with autocommit=False (default) need explicit commit
                # if inside a manual connection (not engine.begin())
                conn.commit() 

        upgrade_schema(today_engine)
        upgrade_schema(all_engine)
        
        last_run_file = os.path.join(BASE_DIR, "last_run_date.txt")
        try:
            with open(last_run_file, "r") as f: last_run_date = f.read()
        except FileNotFoundError: last_run_date = ""

        today_date = datetime.now().strftime("%Y-%m-%d")
        
        if last_run_date != today_date:
            print(f"New day detected. Clearing today's trade log...")
            with today_engine.begin() as conn:
                conn.execute(sql_text("DELETE FROM trades"))
            
            with open(last_run_file, "w") as f: f.write(today_date)
            print("Today's trade log cleared.")

        print("Databases setup complete.")