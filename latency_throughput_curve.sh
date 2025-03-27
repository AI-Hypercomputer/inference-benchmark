#!/bin/bash

set -o xtrace

export IP=$IP

huggingface-cli login --token "$HF_TOKEN" --add-to-git-credential

if [[ "$PROMPT_DATASET" = "sharegpt" ]]; then
  PROMPT_DATASET_FILE="ShareGPT_V3_unfiltered_cleaned_split.json"
fi

PYTHON="python3"
BASE_PYTHON_OPTS=(
  "benchmark_serving.py"
  "--save-json-results"
  "--host=$IP"
  "--port=$PORT"
  "--dataset=$PROMPT_DATASET_FILE"
  "--tokenizer=$TOKENIZER"
  "--backend=$BACKEND"
  "--max-input-length=$INPUT_LENGTH"
  "--max-output-length=$OUTPUT_LENGTH"
  "--file-prefix=$FILE_PREFIX"
  "--models=$MODELS"
  "--pm-namespace=$PM_NAMESPACE"
  "--pm-job=$PM_JOB"
)

[[ "$TRAFFIC_SPLIT" ]] && BASE_PYTHON_OPTS+=("--traffic-split=$TRAFFIC_SPLIT")
[[ "$OUTPUT_BUCKET" ]] && BASE_PYTHON_OPTS+=("--output-bucket=$OUTPUT_BUCKET")
[[ "$SCRAPE_SERVER_METRICS" = "true" ]] && BASE_PYTHON_OPTS+=("--scrape-server-metrics")
[[ "$SAVE_AGGREGATED_RESULT" = "true" ]] && BASE_PYTHON_OPTS+=("--save-aggregated-result")
[[ "$STREAM_REQUEST" = "true" ]] && BASE_PYTHON_OPTS+=("--stream-request")
[[ "$OUTPUT_BUCKET_FILEPATH" ]] && BASE_PYTHON_OPTS+=("--output-bucket-filepath" "$OUTPUT_BUCKET_FILEPATH")

for request_rate in $(echo $REQUEST_RATES | tr ',' ' '); do
  echo "Benchmarking request rate: ${request_rate}"
  timestamp=$(date +"%Y-%m-%d_%H-%M-%S")
  output_file="latency-profile-${timestamp}.txt"
  
  if [ "$request_rate" == "0" ]; then
    request_rate="inf"
    num_prompts=$MAX_NUM_PROMPTS
  else
    num_prompts=$(awk "BEGIN {print int($request_rate * $BENCHMARK_TIME_SECONDS)}")
  fi

  echo "TOTAL prompts: $num_prompts"
  PYTHON_OPTS=("${BASE_PYTHON_OPTS[@]}" "--request-rate=$request_rate" "--num-prompts=$num_prompts")
  
  $PYTHON "${PYTHON_OPTS[@]}" > "$output_file"
  cat "$output_file"
  sleep 30
done

export LPG_FINISHED="true"
sleep infinity
