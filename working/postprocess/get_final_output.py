import pandas as pd

d = pd.read_csv("/working/output/kira-0515-llm-en-split.csv")
d["final_output"] = d["extracted_data"].str.replace("'", '"', regex=False)
d.to_csv("/working/output/test.csv")