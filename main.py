import argparse
import os
import subprocess
import sys


def run_merged_bot():
    from bot import main as merged_main

    merged_main()


def run_legacy_bpm_bot():
    legacy_path = r"D:\DESKTOP\BEATBOT\telegram-audio-bot\bot.py"
    if not os.path.exists(legacy_path):
        raise FileNotFoundError(f"Legacy bot not found: {legacy_path}")
    subprocess.run([sys.executable, legacy_path], check=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Unified launcher for beat bots.")
    parser.add_argument(
        "--bot",
        choices=["merged", "legacy-bpm"],
        default="merged",
        help="merged: unified bot in this project; legacy-bpm: old second bot",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.bot == "legacy-bpm":
        run_legacy_bpm_bot()
    else:
        run_merged_bot()


if __name__ == "__main__":
    main()
