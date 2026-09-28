import argparse
import re
import pandas as pd
from pathlib import Path

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)

TRANSLATION_COLUMNS = ["valoracion_en", "valoracion_thinking", "valoracion_en_clean"]


def split_thinking(text):
    if not isinstance(text, str):
        return "", ""
    thoughts = THINK_PATTERN.findall(text)
    thinking = "\n".join(t.strip() for t in thoughts)
    translation = THINK_PATTERN.sub("", text).strip()
    return thinking, translation


def main():
    parser = argparse.ArgumentParser(
        description="Merge a separately produced translation CSV (e.g. from translate_reports.py) "
        "into a run.py output CSV, joining on patient-identifying key columns."
    )
    parser.add_argument("-i", "--input", required=True, help="run.py output CSV to merge translations into")
    parser.add_argument("-t", "--translation", required=True, help="CSV containing the translation columns")
    parser.add_argument("-o", "--output", required=True, help="Path to write the merged CSV")
    parser.add_argument(
        "-k", "--keys", nargs="+", default=["info_key", "sip"],
        help="Column(s) identifying a row, shared by both CSVs (default: info_key sip)",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    translation_df = pd.read_csv(args.translation, sep=None, engine="python")

    missing_keys = [k for k in args.keys if k not in df.columns or k not in translation_df.columns]
    if missing_keys:
        raise ValueError(f"Key column(s) {missing_keys} missing from --input and/or --translation")

    bring_cols = [c for c in TRANSLATION_COLUMNS if c in translation_df.columns]
    if not bring_cols:
        raise ValueError(
            f"None of {TRANSLATION_COLUMNS} found in {args.translation}. Available: {list(translation_df.columns)}"
        )

    # Drop any of these columns already present in df so the merge doesn't produce
    # _x/_y suffixes -- the translation file's values take precedence.
    df = df.drop(columns=[c for c in bring_cols if c in df.columns])
    merged = df.merge(translation_df[args.keys + bring_cols], on=args.keys, how="left")

    # If the translation file only has the raw (un-split) valoracion_en, derive
    # valoracion_thinking/valoracion_en_clean from it, same as split_thinking.py.
    if "valoracion_en" in merged.columns and "valoracion_en_clean" not in merged.columns:
        split = merged["valoracion_en"].apply(split_thinking)
        merged["valoracion_thinking"] = [s[0] for s in split]
        merged["valoracion_en_clean"] = [s[1] for s in split]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)
    print(f"Saved merged CSV to {output_path}")


if __name__ == "__main__":
    main()
