import argparse
import logging

from config import Config
from sync import sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Add/update TimeTree events; never delete"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show counts without writing"
    )
    args = parser.parse_args()
    Config.validate()
    sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
