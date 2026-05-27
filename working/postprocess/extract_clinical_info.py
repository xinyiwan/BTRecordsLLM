import json
import pandas as pd


def get_clinical_info(pid: str, mapping_csv: str, results_csv: str) -> dict:
    """Look up clinical info for a BONE-AI patient ID.

    Mapping CSV must have columns: subject_code, info_key, sip.
    Results CSV must have columns: info_key, sip, final_output (JSON string).

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

    data = json.loads(record.iloc[0]["final_output"])
    return {
        "symptoms": data.get("Patient Symptoms", []),
        "history_of_neoplasm": data.get("History of neoplasm", "Not specified"),
        "suspected_metastasis": data.get("Suspected metastatic disease", "Not specified"),
        "skeletal_location": data.get("Skeletal Location", "Not specified"),
        "location_within_bone": data.get("Location within bone", "Not specified"),
    }


if __name__ == "__main__":
    import sys
    pid, mapping_csv, results_csv = sys.argv[1], sys.argv[2], sys.argv[3]
    print(json.dumps(get_clinical_info(pid, mapping_csv, results_csv), indent=2))
