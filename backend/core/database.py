import os
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

# --- THIS IS THE FIX: Use the parent directory of 'core' ---
# Get the directory where this script ('database.py') is located, which is the 'core' folder
CORE_DIR = os.path.dirname(os.path.abspath(__file__))

# Get the parent directory of 'core', which is the 'backend' root folder
BASE_DIR = os.path.dirname(CORE_DIR)

# Define the database file names
TODAY_DB_NAME = "trading_data_today.db"
ALL_DB_NAME = "trading_data_all.db"

# Create the full, absolute paths to the database files
# os.path.join will now place them in the 'backend' root directory
TODAY_DB_PATH = os.path.join(BASE_DIR, TODAY_DB_NAME)
ALL_DB_PATH = os.path.join(BASE_DIR, ALL_DB_NAME)

# SQLAlchemy database URLs for SQLite using the absolute paths
DATABASE_URL_TODAY = f"sqlite:///{TODAY_DB_PATH}"
DATABASE_URL_ALL = f"sqlite:///{ALL_DB_PATH}"

# Create a shared engine for each database.
today_engine = create_engine(
    DATABASE_URL_TODAY,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=2
)

all_engine = create_engine(
    DATABASE_URL_ALL,
    connect_args={"check_same_thread": False},
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=2
)

# Export the 'text' function for convenience
sql_text = text