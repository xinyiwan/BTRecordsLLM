import argparse
import json
import pandas as pd


def _parse(extracted_data: str) -> dict:
    data = json.loads(extracted_data.replace("'", '"'))
    return {
        "symptoms": data.get("Patient Symptoms", []),
        "history_of_neoplasm": data.get("History of neoplasm", "Not specified"),
        "suspected_metastasis": data.get("Suspected metastatic disease", "Not specified"),
        "skeletal_location": data.get("Skeletal Location", "Not specified"),
        "location_within_bone": data.get("Location within bone", "Not specified"),
    }


def get_clinical_info(pid: str, mapping_csv: str, results_csv: str) -> dict:
    """Look up clinical info for a BONE-AI patient ID.

    Mapping CSV must have columns: subject_code, info_key, sip.
    Results CSV must have columns: info_key, sip, extracted_data.

    Returns dict with keys: symptoms, history_of_neoplasm,
    suspected_metastasis, skeletal_location, location_within_bone.
    """
    mapping = pd.read_csv(mapping_csv)
    results = pd.read_csv(results_csv)

    row = mapping[mapping["subject_code"] == pid]
    if row.empty:
        raise KeyError(f"Patient {pid} not found in mapping CSV")
    info_key = row.iloc[0]["info_key"]
    sip = row.iloc[0]["sip"]

    record = results[(results["info_key"] == info_key) & (results["sip"] == sip)]
    if record.empty:
        raise KeyError(f"No record for {pid} (info_key={info_key}, sip={sip})")

    return _parse(record.iloc[0]["extracted_data"])


def build_clinical_csv(mapping_csv: str, results_csv: str, output_csv: str) -> None:
    """Extract clinical info for every PID in the mapping CSV and save to one CSV."""
    mapping = pd.read_csv(mapping_csv)
    results = pd.read_csv(results_csv)

    merged = mapping.merge(
        results[["info_key", "sip", "extracted_data"]],
        on=["info_key", "sip"],
        how="left",
    )

    rows = []
    for _, r in merged.iterrows():
        pid = r["subject_code"]
        info_key = r["info_key"]
        sip = r["sip"]
        try:
            rows.append({"pid": pid, "info_key": info_key, "sip": sip, **_parse(r["extracted_data"])})
        except (json.JSONDecodeError, TypeError, AttributeError):
            rows.append({"pid": pid, "info_key": info_key, "sip": sip,
                         "symptoms": None, "history_of_neoplasm": None,
                         "suspected_metastasis": None, "skeletal_location": None,
                         "location_within_bone": None})

    pd.DataFrame(rows).to_csv(output_csv, index=False)
    print(f"Saved {len(rows)} rows to {output_csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True, help="CSV with subject_code, info_key, sip")
    ap.add_argument("--results", required=True, help="CSV with info_key, sip, extracted_data")
    ap.add_argument("--pid", help="If set, print info for this PID; otherwise build full CSV")
    ap.add_argument("--output", help="Destination CSV (required when --pid is not set)")
    args = ap.parse_args()

    if args.pid:
        print(json.dumps(get_clinical_info(args.pid, args.mapping, args.results), indent=2))
    else:
        if not args.output:
            ap.error("--output is required when --pid is not provided")
        build_clinical_csv(args.mapping, args.results, args.output)
