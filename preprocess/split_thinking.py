import argparse
import re
import pandas as pd
from pathlib import Path

THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def split_thinking(text):
    if not isinstance(text, str):
        return "", ""
    thoughts = THINK_PATTERN.findall(text)
    thinking = "\n".join(t.strip() for t in thoughts)
    translation = THINK_PATTERN.sub("", text).strip()
    return thinking, translation


def main():
    parser = argparse.ArgumentParser(description="Split <think>...</think> reasoning out of the translation column.")
    parser.add_argument("-i", "--input", default="data/test-100-en.csv", help="Input CSV path")
    parser.add_argument("-o", "--output", default="data/test-100-en-split.csv", help="Output CSV path")
    parser.add_argument("-c", "--column", default="valoracion_en", help="Column containing the raw model output")
    parser.add_argument("--thinking-column", default="valoracion_thinking", help="New column for the thinking content")
    parser.add_argument("--translation-column", default="valoracion_en_clean", help="New column for the cleaned translation")
    args = parser.parse_args()

    df = pd.read_csv(args.input, sep=",")
    if args.column not in df.columns:
        raise ValueError(f"Column '{args.column}' not found in {args.input}. Available: {list(df.columns)}")

    split = df[args.column].apply(split_thinking)
    df[args.thinking_column] = [s[0] for s in split]
    df[args.translation_column] = [s[1] for s in split]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, sep=",")
    print(f"Saved split CSV to {output_path}")


if __name__ == "__main__":
    main()
