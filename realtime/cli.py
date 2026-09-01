from __future__ import annotations

import argparse

from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Live search discovery and real-time crawler")
    parser.add_subparsers(dest="command", required=True).add_parser("serve")
    args = parser.parse_args()
    if args.command == "serve":
        serve()


if __name__ == "__main__":
    main()
