import argparse
import json
import re
from pathlib import Path

import pandas as pd

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_final_output(text):
    """Same recovery logic as server.py's parse_final_output."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    if cleaned.lstrip().startswith("{"):
        candidate = cleaned
        for _ in range(3):
            candidate = candidate + "}"
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Backfill corrected_value for existing 'correct' verdicts that have no "
        "explicit true label, filling it with the model's predicted value -- matches the "
        "behavior review_server.py now applies going forward. Existing non-null "
        "corrected_value or verdicts other than 'correct' are left untouched."
    )
    parser.add_argument("-c", "--csv", required=True, help="The CSV that was reviewed (has final_output column)")
    parser.add_argument("-r", "--reviews", required=True, help="Path to the .reviews.json sidecar to backfill")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Where to write the backfilled reviews JSON (default: overwrite --reviews in place)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    with open(args.reviews, encoding="utf-8") as f:
        reviews = json.load(f)

    changed = 0
    for idx_str, entry in reviews.items():
        idx = int(idx_str)
        if idx >= len(df):
            print(f"Warning: row {idx} out of range for {args.csv} ({len(df)} rows), skipping")
            continue
        parsed = parse_final_output(df.iloc[idx].get("final_output", ""))
        if not isinstance(parsed, dict):
            continue
        for field, review in entry.get("fields", {}).items():
            if review.get("verdict") == "correct" and review.get("corrected_value") is None:
                predicted = parsed.get(field)
                if isinstance(predicted, str):
                    review["corrected_value"] = predicted
                    changed += 1

    print(f"{'Would backfill' if args.dry_run else 'Backfilled'} {changed} field(s)")

    if not args.dry_run:
        output_path = Path(args.output) if args.output else Path(args.reviews)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(reviews, f, indent=2, sort_keys=True)
        print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
