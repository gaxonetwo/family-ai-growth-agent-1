"""Lightweight tests; run after generating the CSV dataset."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

def test_required_csv_files_exist():
    for name in ["users", "devices", "events", "feedback", "payments"]:
        assert (RAW / f"{name}.csv").exists()

def test_user_ids_are_unique():
    users = pd.read_csv(RAW / "users.csv")
    assert users["user_id"].is_unique

def test_event_users_exist():
    users = pd.read_csv(RAW / "users.csv")
    events = pd.read_csv(RAW / "events.csv")
    assert set(events["user_id"]).issubset(set(users["user_id"]))
