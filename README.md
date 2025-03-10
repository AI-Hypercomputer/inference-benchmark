# Inference Benchmark

A model server agnostic inference benchmarking tool that can be used to
benchmark LLMs running on differet infrastructure like GPU and TPU. It can also
be run on a GKE cluster as a container.

## Run the benchmark

1. Create a python virtualenv.

2. Install all the prerequisite packages.

```
pip install -r requirements.txt
```

3. Set your huggingface token as an enviornment variable

```
export HF_TOKEN=<your-huggingface-token>
```

4. Download the ShareGPT dataset.

```
wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json
```

5. Run the benchmarking script directly with a specific request rate.

```
python3 benchmark_serving.py --save-json-results --host=$IP  --port=$PORT --dataset=$PROMPT_DATASET_FILE --tokenizer=$TOKENIZER --request-rate=$REQUEST_RATE --backend=$BACKEND --num-prompts=$NUM_PROMPTS --max-input-length=$INPUT_LENGTH --max-output-length=$OUTPUT_LENGTH --file-prefix=$FILE_PREFIX
```

6. Generate a full latency profile which generates latency and throughput data
   at different request rates.

```
./latency_throughput_curve.sh
```

7. You can build a container to run the benchmark directly on a Kubernetes cluster
using the specified Dockerfile.

```
docker build -t inference-benchmark .
```
