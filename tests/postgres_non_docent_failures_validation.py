"""Explicit PG-02 rollback/retry tests; owns a new PostgreSQL cluster only."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.postgres_adc_staff_groups_validation import main

if __name__ == '__main__':
    raise SystemExit(main(failure_cases=True))
