"""Basic quality checks for generated CSV files."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"

def main():
    names = ["users", "devices", "events", "feedback", "payments"]
    data = {name: pd.read_csv(RAW / f"{name}.csv") for name in names}
    errors = []

    def check_unique(df, col, label):
        if df[col].duplicated().any():
            errors.append(f"{label}.{col} contains duplicates")

    check_unique(data["users"], "user_id", "users")
    check_unique(data["devices"], "device_id", "devices")
    check_unique(data["events"], "event_id", "events")
    check_unique(data["feedback"], "feedback_id", "feedback")
    check_unique(data["payments"], "payment_id", "payments")

    user_ids = set(data["users"]["user_id"])
    for table in ["devices", "events", "feedback", "payments"]:
        unknown = set(data[table]["user_id"]) - user_ids
        if unknown:
            errors.append(f"{table} contains {len(unknown)} unknown user_id values")

    device_ids = set(data["devices"]["device_id"])
    event_device_ids = set(data["events"]["device_id"].dropna())
    if event_device_ids - device_ids:
        errors.append("events contains device_id values missing from devices")

    allowed_events = {
        "device_bind", "onboarding_start", "onboarding_complete", "app_open",
        "ai_interaction", "feature_use", "device_offline", "device_online"
    }
    invalid_events = set(data["events"]["event_name"]) - allowed_events
    if invalid_events:
        errors.append(f"Unknown event names: {sorted(invalid_events)}")

    if (data["payments"]["amount"] < 0).any():
        errors.append("payments contains negative amounts")

    print("Row counts:")
    for name, df in data.items():
        print(f"  {name}: {len(df):,}")

    if errors:
        print("\nDATA QUALITY: FAILED")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(1)

    print("\nDATA QUALITY: PASSED")

if __name__ == "__main__":
    main()
