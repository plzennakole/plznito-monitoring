# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Download one Plznito ticket and print it as JSON (debugging helper)."""

import argparse
import json

from plznito_monitoring.restore_all import download_one_id


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("id", type=int)
    parser.add_argument("--source", type=str, default="auto", choices=["auto", "api", "web"])
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    data = download_one_id(args.id, source=args.source)
    indent = 2 if args.pretty else None
    print(json.dumps(data, indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()
