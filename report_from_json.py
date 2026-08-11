#!/usr/bin/env python3
"""Re-render a report from a result.json that fancyfilesync wrote earlier.

No scanning, no hashing, no SSH -- it only reads the JSON file.

    ./report_from_json.py result.json                  # text report to stdout
    ./report_from_json.py result.json --md report.md   # Markdown to a file
    ./report_from_json.py result.json --show-remote-only --max-examples 20

    # Summarise the local files with no match by the directory holding them,
    # flagging whether each directory is entirely unmatched (copy it wholesale)
    # or also holds files that are already on the other side:
    ./report_from_json.py result.json --local-only-dirs
    ./report_from_json.py result.json --local-only-dirs --md to_copy.md

Equivalent to `python -m fancyfilesync.jsonload result.json`.
"""

import sys

from fancyfilesync.jsonload import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
