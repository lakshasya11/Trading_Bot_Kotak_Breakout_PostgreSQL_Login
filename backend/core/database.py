import os
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# --- BACKEND ROOT DIRECTORY ---
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CORE_DIR)

# Get PostgreSQL credentials from environment
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME_TODAY = os.getenv("DB_NAME_TODAY", "trading_kotak_today")
DB_NAME_ALL = os.getenv("DB_NAME_ALL", "trading_kotak_all")

# PostgreSQL connection strings
DATABASE_URL_TODAY = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME_TODAY}"
DATABASE_URL_ALL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME_ALL}"

# Create engines with PostgreSQL pool configuration
today_engine = create_engine(
    DATABASE_URL_TODAY,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=5,
    pool_recycle=3600
)

all_engine = create_engine(
    DATABASE_URL_ALL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=5,
    pool_recycle=3600
)

# Export the 'text' function for convenience
sql_text = text