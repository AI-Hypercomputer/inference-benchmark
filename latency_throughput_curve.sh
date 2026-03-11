#!/bin/bash
set -euo pipefail
set -o xtrace

export IP=${IP:-localhost}

huggingface-cli login --token "${HF_TOKEN:-}" --add-to-git-credential || true

if [[ "${PROMPT_DATASET:-}" == "sharegpt" ]]; then
  PROMPT_DATASET_FILE="ShareGPT_V3_unfiltered_cleaned_split.json"
else
  PROMPT_DATASET_FILE="${PROMPT_DATASET_FILE:-$PROMPT_DATASET}"
fi

PYTHON="python3"
BASE_PYTHON_OPTS=(
  "benchmark_serving.py"
  "--save-json-results"
  "--host=$IP"
  "--port=${PORT:-7080}"
  "--dataset=$PROMPT_DATASET_FILE"
  "--tokenizer=$TOKENIZER"
  "--backend=$BACKEND"
  "--max-input-length=$INPUT_LENGTH"
  "--max-output-length=$OUTPUT_LENGTH"
  "--file-prefix=$FILE_PREFIX"
  "--models=$MODELS"
  "--pm-namespace=${PM_NAMESPACE:-default}"
  "--pm-job=${PM_JOB:-vllm-podmonitoring}"
)

[[ "${MIN_INPUT_LENGTH:-}" ]]        && BASE_PYTHON_OPTS+=("--min-input-length=$MIN_INPUT_LENGTH")
[[ "${MIN_OUTPUT_LENGTH:-}" ]]       && BASE_PYTHON_OPTS+=("--min-output-length=$MIN_OUTPUT_LENGTH")
[[ "${OUTPUT_BUCKET:-}" ]]           && BASE_PYTHON_OPTS+=("--output-bucket=$OUTPUT_BUCKET")
[[ "${TRAFFIC_SPLIT:-}" ]]           && BASE_PYTHON_OPTS+=("--traffic-split=$TRAFFIC_SPLIT")
[[ "${SCRAPE_SERVER_METRICS:-}" == "true" ]] && BASE_PYTHON_OPTS+=("--scrape-server-metrics")
[[ "${SAVE_AGGREGATED_RESULT:-}" == "true" ]] && BASE_PYTHON_OPTS+=("--save-aggregated-result")
[[ "${STREAM_REQUEST:-}" == "true" ]] && BASE_PYTHON_OPTS+=("--stream-request")
[[ "${IGNORE_EOS:-}" == "true" ]]     && BASE_PYTHON_OPTS+=("--ignore-eos")
[[ "${OUTPUT_BUCKET_FILEPATH:-}" ]]   && BASE_PYTHON_OPTS+=("--output-bucket-filepath" "$OUTPUT_BUCKET_FILEPATH")
[[ "${TCP_CONN_LIMIT:-}" ]]           && BASE_PYTHON_OPTS+=("--tcp-conn-limit" "$TCP_CONN_LIMIT")
[[ "${SPANNER_INSTANCE_ID:-}" ]]      && BASE_PYTHON_OPTS+=("--spanner-instance-id" "$SPANNER_INSTANCE_ID")
[[ "${SPANNER_DATABASE_ID:-}" ]]      && BASE_PYTHON_OPTS+=("--spanner-database-id" "$SPANNER_DATABASE_ID")

# Support TARGETMODELS or TARGET_MODELS
TARGETMODELS_EFFECTIVE="${TARGETMODELS:-${TARGET_MODELS:-}}"
[[ "$TARGETMODELS_EFFECTIVE" ]] && BASE_PYTHON_OPTS+=("--targetmodels" "$TARGETMODELS_EFFECTIVE")

SLEEP_TIME=${SLEEP_TIME:-0}
POST_BENCHMARK_SLEEP_TIME=${POST_BENCHMARK_SLEEP_TIME:-infinity}

if [[ -z "${REQUEST_RATES:-}" ]]; then
  echo "ERROR: REQUEST_RATES is empty"; exit 1
fi

for request_rate in $(echo "$REQUEST_RATES" | tr ',' ' '); do
  echo "Benchmarking request rate: ${request_rate}"
  timestamp=$(date +"%Y-%m-%d_%H-%M-%S")
  output_file="latency-profile-${timestamp}.txt"

  if [[ "$request_rate" == "0" ]]; then
    request_rate="inf"
    num_prompts="${MAX_NUM_PROMPTS:?Set MAX_NUM_PROMPTS when REQUEST_RATE=0}"
  else
    num_prompts=$(awk "BEGIN {print int($request_rate * ${BENCHMARK_TIME_SECONDS:-60})}")
  fi

  echo "TOTAL prompts: $num_prompts"
  PYTHON_OPTS=("${BASE_PYTHON_OPTS[@]}" "--request-rate=$request_rate" "--num-prompts=$num_prompts")

  $PYTHON "${PYTHON_OPTS[@]}" > "$output_file"
  cat "$output_file"
  echo "Sleeping for $SLEEP_TIME seconds..."
  sleep "$SLEEP_TIME"
done

export LPG_FINISHED="true"
sleep "$POST_BENCHMARK_SLEEP_TIME"
