import argparse
import logging
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SYSTEM_PROMPT = (
    "You are a professional medical translator. "
    "Translate the Spanish radiology report below into English. "
    "Preserve all clinical meaning, measurements, anatomical terms, and structure (line breaks, sections). "
    "Do not summarize, interpret, or add commentary. "
    "Return ONLY the translated text, without quotes or any preface."
)


def translate(llm: ChatOpenAI, text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=text),
    ])
    return response.content.strip()


def main():
    parser = argparse.ArgumentParser(description="Translate Spanish 'valoracion' reports into English using a local LLM.")
    parser.add_argument("-i", "--input", default="data/test-100.csv", help="Input CSV path")
    parser.add_argument("-o", "--output", default="data/test-100_en.csv", help="Output CSV path")
    parser.add_argument("-c", "--column", default="valoracion", help="Column to translate")
    parser.add_argument("--out-column", default="valoracion_en", help="Column name for the English translation")
    parser.add_argument("-u", "--base-url", default="http://localhost:8765/v1", help="Local LLM (OpenAI-compatible) base URL")
    parser.add_argument("--api-key", default="DummyAPIKey", help="API key for the local LLM server")
    parser.add_argument("-m", "--model", default="Qwen/Qwen3-1.7B", help="Model name as served by the local LLM")
    parser.add_argument("-t", "--temperature", type=float, default=0.2, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens for the response")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds")
    parser.add_argument("-n", "--limit", type=int, default=None, help="Optional: only translate the first N rows (for testing)")
    args = parser.parse_args()

    df = pd.read_csv(args.input, sep=";")
    if args.column not in df.columns:
        raise ValueError(f"Column '{args.column}' not found in {args.input}. Available: {list(df.columns)}")

    if args.limit is not None:
        df = df.head(args.limit).copy()

    llm = ChatOpenAI(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )

    logging.info(f"Translating {len(df)} rows from '{args.column}' using model '{args.model}' at {args.base_url}")

    translations = []
    for text in tqdm(df[args.column].tolist(), desc="Translating"):
        try:
            translations.append(translate(llm, text))
        except Exception as e:
            logging.error(f"Translation failed: {e}")
            translations.append("")

    df[args.out_column] = translations

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, sep=";")
    logging.info(f"Saved translated CSV to {output_path}")


if __name__ == "__main__":
    main()
