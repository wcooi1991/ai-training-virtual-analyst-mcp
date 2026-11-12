
import json
import os
import re
import sqlite3
import sqlparse
import streamlit as st
from google.genai import types
from google.adk.agents import Agent
from google.adk.models import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

# ==============================================================================
# 1. Page Configuration
# ==============================================================================
st.set_page_config(
    page_title="MCP Banking Virtual Analyst",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==============================================================================
# 2. Secret & API Key Resolution (Streamlit Cloud Friendly)
# ==============================================================================
api_key = ""
try:
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
except Exception:
    # Graceful fallback if secrets.toml is not present locally
    api_key = os.environ.get("GOOGLE_API_KEY", "")

if api_key:
    os.environ["GOOGLE_API_KEY"] = api_key

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

DB_FILE = "bank11.sqlite"
SCHEMA_FILE = "bank_schema.txt"

# Guardrail for missing key in Streamlit Cloud
if not os.environ.get("GOOGLE_API_KEY"):
    st.error(
        "⚠️ **GOOGLE_API_KEY is not configured!**\n\n"
        "Please go to your Streamlit Cloud Dashboard: **App Settings** -> **Secrets**, "
        "and add:\n```toml\nGOOGLE_API_KEY = \"your_gemini_api_key_here\"\n```"
    )
    st.stop()

# ==============================================================================
# 3. Database Bootstrap (Ensures App Runs Immediately on Fresh Repos)
# ==============================================================================
def ensure_database_and_schema():
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS branches (
            branch_id TEXT PRIMARY KEY,
            branch_name TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            region TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            full_name TEXT NOT NULL,
            customer_segment TEXT NOT NULL,
            join_date TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(branch_id)
        );

        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            account_type TEXT NOT NULL,
            balance REAL NOT NULL,
            currency TEXT DEFAULT 'MYR',
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS loan_applications (
            application_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            loan_type TEXT NOT NULL,
            applied_amount REAL NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        INSERT OR IGNORE INTO branches VALUES
            ('BR_001', 'Petaling Jaya Main', 'Petaling Jaya', 'Selangor', 'Central'),
            ('BR_002', 'Kepong Metro Prima', 'Kuala Lumpur', 'W.P. Kuala Lumpur', 'Central'),
            ('BR_003', 'KLCC Corporate Banking', 'Kuala Lumpur', 'W.P. Kuala Lumpur', 'Central'),
            ('BR_004', 'Georgetown Heritage', 'George Town', 'Penang', 'Northern'),
            ('BR_005', 'Johor Bahru City Square', 'Johor Bahru', 'Johor', 'Southern');

        INSERT OR IGNORE INTO customers VALUES
            ('CUST_901', 'BR_001', 'Tan Wei Ming', 'Premier Banking', '2023-01-15'),
            ('CUST_902', 'BR_001', 'Ahmad Farhan', 'Mass Affluent', '2023-03-22'),
            ('CUST_903', 'BR_001', 'Siti Nurhaliza', 'Mass Retail', '2024-06-10'),
            ('CUST_904', 'BR_001', 'Kavitha Pillai', 'Premier Banking', '2024-08-01'),
            ('CUST_905', 'BR_002', 'Chong Chee Keong', 'Mass Retail', '2022-11-05'),
            ('CUST_906', 'BR_002', 'Faridah binti Osman', 'Mass Affluent', '2023-09-19'),
            ('CUST_907', 'BR_002', 'Lee Kuan Yew', 'Premier Banking', '2024-02-14'),
            ('CUST_908', 'BR_003', 'Goh Chok Tong', 'Private Wealth', '2021-05-18'),
            ('CUST_909', 'BR_003', 'Zainal Abidin', 'Corporate Banking', '2022-04-30'),
            ('CUST_910', 'BR_004', 'Lim Guan Eng', 'Mass Affluent', '2023-07-09');

        INSERT OR IGNORE INTO accounts VALUES
            ('ACC_1001', 'CUST_901', 'Savings', 45200.50, 'MYR'),
            ('ACC_1002', 'CUST_902', 'Current', 12340.00, 'MYR'),
            ('ACC_1003', 'CUST_903', 'Savings', 6800.75, 'MYR'),
            ('ACC_1004', 'CUST_904', 'Fixed Deposit', 150000.00, 'MYR'),
            ('ACC_1005', 'CUST_905', 'Savings', 3200.00, 'MYR'),
            ('ACC_1006', 'CUST_906', 'Current', 28900.20, 'MYR'),
            ('ACC_1007', 'CUST_907', 'Fixed Deposit', 300000.00, 'MYR'),
            ('ACC_1008', 'CUST_908', 'Investment', 1250000.00, 'MYR'),
            ('ACC_1009', 'CUST_909', 'Corporate Current', 489000.00, 'MYR'),
            ('ACC_1010', 'CUST_910', 'Savings', 9800.00, 'MYR');

        INSERT OR IGNORE INTO loan_applications VALUES
            ('LOAN_801', 'CUST_901', 'Car Loan', 85000.00, 'APPROVED'),
            ('LOAN_802', 'CUST_902', 'Mortgage', 520000.00, 'UNDER_REVIEW'),
            ('LOAN_803', 'CUST_903', 'Personal Loan', 15000.00, 'APPROVED'),
            ('LOAN_804', 'CUST_905', 'Car Loan', 65000.00, 'APPROVED'),
            ('LOAN_805', 'CUST_906', 'SME Business Loan', 250000.00, 'REJECTED');
        """)
        conn.commit()
        conn.close()

    if not os.path.exists(SCHEMA_FILE):
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        ddl = [row[0] for row in cur.fetchall() if row[0]]
        conn.close()
        with open(SCHEMA_FILE, "w", encoding="utf-8") as f:
            f.write("\n\n".join(ddl))

ensure_database_and_schema()

# ==============================================================================
# 4. MCP Protocol Primitives & Security Guardrails
# ==============================================================================
def is_sql_mutation(sql_query: str) -> bool:
    """AST-based parser that rejects any DDL/DML mutation statement."""
    cleaned = sql_query.strip().strip(";")
    parsed = sqlparse.parse(cleaned)
    if not parsed:
        return False
    forbidden = {
        'DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'TRUNCATE',
        'PRAGMA', 'REPLACE', 'CREATE', 'RENAME', 'ATTACH', 'DETACH'
    }
    for stmt in parsed:
        if stmt.get_type() != 'SELECT':
            return True
        for token in stmt.flatten():
            if token.value.upper() in forbidden:
                return True
    return False

def anonymize_text(text: str) -> str:
    """Masks banking primary/foreign keys to comply with PDPA."""
    text = re.sub(r'\b(CUST_)[0-9A-Za-z]+([0-9A-Za-z]{2})\b', r'\1***\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(BR_)[0-9A-Za-z]+([0-9A-Za-z]{2})\b', r'\1***\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(ACC_)[0-9A-Za-z]+([0-9A-Za-z]{2})\b', r'\1***\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(LOAN_)[0-9A-Za-z]+([0-9A-Za-z]{2})\b', r'\1***\2', text, flags=re.IGNORECASE)
    return text

def mcp_list_tables() -> str:
    """MCP Tool: Discovers all accessible data tables in the core banking database."""
    try:
        conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cur.
