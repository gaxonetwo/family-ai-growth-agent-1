"""Run the complete Phase 1 data pipeline from the project root."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.generate_data import generate
from src.data.validate_data import main as validate
from src.data.load_sqlite import main as load

if __name__ == "__main__":
    generate(users_n=1000, days=90, seed=42)
    validate()
    load()
    print("\nPhase 1 data pipeline completed.")
