#!/bin/bash
# Runs run.py against Use_Case_BT_Imaging_Features.yaml on Snellius: starts a
# vLLM OpenAI-compatible server for MODEL, waits for it to come up, runs the
# extraction, then tears the server down. No Docker/Apptainer here -- this
# repo (unlike Bone_CLS) has no container image, so the job just activates
# the single plain venv built from requirements.txt.
#
# requirements.txt pins vllm==0.18.1, not the latest vllm -- 0.19+ requires
# openai>=2.0.0, which conflicts with langchain-openai==0.3.30's
# openai<2.0.0 pin (used by parser.py's ChatOpenAI client). 0.10.1-0.18.1 all
# require openai>=1.99.1 (0.17+ caps <2.25.0), which overlaps with
# langchain-openai's >=1.99.9,<2.0.0 window, so both resolve into one venv.
#
#SBATCH --job-name=bt_imaging_features
#SBATCH --partition=gpu_h100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=04:00:00
#SBATCH --output=/projects/prjs1779/BONE-AI/logs/out/slurm-%x-%j.out
#SBATCH --error=/projects/prjs1779/BONE-AI/logs/err/slurm-%x-%j.err

set -euo pipefail

export HF_HOME=/scratch-shared/$USER/hf-cache

# Absolute, because sbatch copies this script to a node-local spool dir
# before running it -- ${BASH_SOURCE[0]} would resolve outside the repo.
REPO=/gpfs/work2/0/prjs1779/BONE-AI/BTRecordsLLM

# --- edit these for your run ---
MODEL=/scratch-shared/$USER/models/<your-model-dir>
PROMPT_CONFIG=$REPO/resources/prompt_configs/Use_Case_BT_Imaging_Features.yaml
PARAMS_CONFIG=$REPO/resources/model_configs/DeepSeek-R1-0528-Qwen3-8B.yaml
INPUT=/projects/prjs1779/BONE-AI/data/<your-input>.csv
FORMAT=csv
PROMPT_METHOD=FewShot
TENSOR_PARALLEL_SIZE=1

OUTDIR=/scratch-shared/$USER/BONE-AI/imaging_features
OUT=$OUTDIR/imaging_features_$(basename "$MODEL").csv
PORT=8765
BASE_URL="http://localhost:${PORT}/v1"

# run.py sends this exact string as the "model" field of every chat request
# (it's ChatOpenAI(model_name=params_config['model']) in parser.py) -- it
# must match what vLLM reports serving, so read it straight from
# PARAMS_CONFIG rather than letting vLLM default to $MODEL's local path.
mkdir -p "$OUTDIR"
cd "$REPO"
source venv/bin/activate

SERVED_MODEL_NAME=$(python -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['model'])" "$PARAMS_CONFIG")
# An empty value here is passed to vLLM as --served-model-name "", which
# vLLM silently treats as unset and falls back to serving under $MODEL's
# raw path instead -- so run.py's requests 404 with no obvious cause. Fail
# loudly here instead of letting that repeat.
: "${SERVED_MODEL_NAME:?failed to read 'model' key from $PARAMS_CONFIG}"

echo "[Starting vLLM] $MODEL as '$SERVED_MODEL_NAME' on port $PORT"
vllm serve "$MODEL" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --dtype bfloat16 \
  --trust-remote-code \
  > "$OUTDIR/vllm_server_${SLURM_JOB_ID}.log" 2>&1 &
VLLM_PID=$!

cleanup() {
  echo "[Stopping vLLM] pid $VLLM_PID"
  kill "$VLLM_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "[Waiting] for vLLM server to become ready..."
until curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/models" | grep -q 200; do
  sleep 5
done
echo "[Ready] vLLM server is up"

python run.py \
  --input "$INPUT" \
  --output "$OUT" \
  --format "$FORMAT" \
  --prompt-method "$PROMPT_METHOD" \
  --prompt-config "$PROMPT_CONFIG" \
  --params-config "$PARAMS_CONFIG" \
  --base-url "$BASE_URL"

echo "[Done] output written to $OUT"
