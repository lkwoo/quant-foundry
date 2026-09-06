"""Minimal, read-only project entry point."""
import argparse
from .strategies.registry import STRATEGIES


def main():
    parser = argparse.ArgumentParser(description="QuantFoundry")
    parser.add_argument("command", choices=["strategies"])
    args = parser.parse_args()
    if args.command == "strategies":
        for name, strategy in STRATEGIES.items():
            print(f"{name} v{strategy.version}")
