"""Backfill persons table for existing profiles that lack a person_id."""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawler.db import Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
LOGGER = logging.getLogger('backfill_persons')


def main():
    db = Database()
    total = 0
    while True:
        count = db.backfill_persons(limit=500)
        if count == 0:
            break
        total += count
        LOGGER.info("Backfilled %d profiles (total: %d)", count, total)
    LOGGER.info("Done. Total profiles linked to persons: %d", total)


if __name__ == '__main__':
    main()
