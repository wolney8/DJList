"""Translate preview-runner flags without relaxing the production server guards."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=3000)
parser.add_argument("--strictPort", action="store_true")
args = parser.parse_args()
# The app deliberately stays on loopback. Only the daemon-selected port changes.
raise SystemExit(main(["--port", str(args.port), "--data-dir", "/tmp/djlist-ui-qa", "--no-browser"]))
