"""Load generated CSV files into a local SQLite database."""
from pathlib import Path
import sqlite3
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
DB_DIR = ROOT / "data" / "processed"
DB_PATH = DB_DIR / "family_ai.db"
SCHEMA = ROOT / "database" / "schema.sql"

def main():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        for table in ["users", "devices", "events", "feedback", "payments"]:
            df = pd.read_csv(RAW / f"{table}.csv")
            df.to_sql(table, conn, if_exists="append", index=False)
        conn.commit()

        print(f"SQLite database created: {DB_PATH}")
        for table in ["users", "devices", "events", "feedback", "payments"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count:,} rows")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
