import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Build clean ground-truth labels and per-feature LLM performance "
        "from a .reviews.json sidecar.\n\n"
        "Rules:\n"
        "  - Clean label: corrected_value wherever it is non-null, regardless of verdict "
        "(covers 'not_in_report' rows that still carry a value). Left blank when null.\n"
        "  - Performance: excludes verdict == 'not_in_report'; everything else "
        "(correct/incorrect) counts, including 'incorrect' rows with no corrected_value."
    )
    parser.add_argument("-r", "--reviews", required=True, help="Path to .reviews.json")
    parser.add_argument("-o", "--out-labels", default=None, help="Output CSV for clean labels")
    parser.add_argument("-p", "--out-perf", default=None, help="Output CSV for per-feature performance")
    args = parser.parse_args()

    reviews_path = Path(args.reviews)
    with reviews_path.open(encoding="utf-8") as f:
        reviews = json.load(f)

    out_labels = Path(args.out_labels) if args.out_labels else reviews_path.with_suffix("").with_suffix(".clean_labels.csv")
    out_perf = Path(args.out_perf) if args.out_perf else reviews_path.with_suffix("").with_suffix(".performance.csv")

    all_features = sorted({feat for rec in reviews.values() for feat in rec.get("fields", {})})

    label_rows = []
    perf = {feat: {"correct": 0, "incorrect": 0} for feat in all_features}

    for rid, rec in sorted(reviews.items(), key=lambda kv: int(kv[0])):
        row = {
            "row_id": rid,
            "info_key": rec.get("info_key", ""),
            "sip": rec.get("sip", ""),
            "subject_code": rec.get("subject_code", ""),
        }
        fields = rec.get("fields", {})
        for feat in all_features:
            info = fields.get(feat, {})
            verdict = info.get("verdict")
            corrected = info.get("corrected_value")
            row[feat] = corrected if corrected is not None else ""
            if verdict in ("correct", "incorrect"):
                perf[feat][verdict] += 1
        label_rows.append(row)

    with out_labels.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["row_id", "info_key", "sip", "subject_code"] + all_features)
        writer.writeheader()
        writer.writerows(label_rows)

    with out_perf.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["feature", "correct", "incorrect", "n_scored", "accuracy"])
        for feat in all_features:
            c, i = perf[feat]["correct"], perf[feat]["incorrect"]
            n = c + i
            acc = c / n if n else float("nan")
            writer.writerow([feat, c, i, n, f"{acc:.4f}" if n else ""])

    print(f"Wrote clean labels ({len(label_rows)} rows) to {out_labels}")
    print(f"Wrote performance report to {out_perf}")


if __name__ == "__main__":
    main()
