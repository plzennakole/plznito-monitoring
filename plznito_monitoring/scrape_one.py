import argparse
import json

from restore_all import download_one_id


def main():
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

