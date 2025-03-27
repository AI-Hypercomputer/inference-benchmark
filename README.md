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

## Run on a Kubernetes cluster
1. You can build a container to run the benchmark directly on a Kubernetes cluster
using the specified Dockerfile.

```
docker build -t inference-benchmark .
```

2. Create a repository in artifact registry to push the image there and use it on your cluster.

```
gcloud artifacts repositories create ai-benchmark --location=us-central1 --repository-format=docker
```

3. Push the image to that repository.

```
docker tag inference-benchmark us-central1-docker.pkg.dev/{project-name}/ai-benchmark/inference-benchmark
docker push us-central1-docker.pkg.dev/{project-name}/ai-benchmark/inference-benchmark
```

4. Update the image name in deploy/deployment.yaml to `us-central1-docker.pkg.dev/{project-name}/ai-benchmark/inference-benchmark`.

5. Deploy and run the benchmark.

```
kubectl apply -f deploy/deployment.yaml
```

6. Get the benchmarking data by looking at the logs of the deployment.

```
kubectl logs deployment/latency-profile-generator
```

7. To download the full report, get it from the container by listing the files and copying it. 
If you specify a GCS bucket, the report will be automatically uploaded there.

```
kubectl exec <latency-profile-generator-pod-name> -- ls
kubectl cp <latency-profile-generator-pod-name>:benchmark-<timestamp>.json report.json
```

8. Delete the benchmarking deployment.

```
kubectl delete -f deploy/deployment.yaml
```

## Configuring the Benchmark

The following are the set of flags the benchmarking script takes in. These are all exposed as environment variables in the `deploy/deployment.yaml` file that you can configure.

* `--backend`:
    * Type: `str`
    * Default: `"vllm"`
    * Choices: `["vllm", "tgi", "naive_transformers", "tensorrt_llm_triton", "sax", "jetstream"]`
    * Description: Specifies the backend model server to benchmark.
* `--file-prefix`:
    * Type: `str`
    * Default: `"benchmark"`
    * Description: Prefix for output files.
* `--endpoint`:
    * Type: `str`
    * Default: `"generate"`
    * Description: The endpoint to send requests to.
* `--host`:
    * Type: `str`
    * Default: `"localhost"`
    * Description: The host address of the server.
* `--port`:
    * Type: `int`
    * Default: `7080`
    * Description: The port number of the server.
* `--dataset`:
    * Type: `str`
    * Description: Path to the dataset. The default dataset used is ShareGPT from HuggingFace.
* `--models`:
    * Type: `str`
    * Description: Comma separated list of models to benchmark.
* `--traffic-split`:
    * Type: parsed traffic split (comma separated list of floats that sum to 1.0)
    * Default: None
    * Description: Comma-separated list of traffic split proportions for the models, e.g. '0.9,0.1'. Sum must equal 1.0.
* `--stream-request`:
    * Action: `store_true`
    * Description: Whether to stream the request. Needed for TTFT metric.
* `--request-timeout`:
    * Type: `float`
    * Default: `3.0 * 60.0 * 60.0` (3 hours)
    * Description: Individual request timeout.
* `--tokenizer`:
    * Type: `str`
    * Required: `True`
    * Description: Name or path of the tokenizer. You can specify the model ID in HuggingFace for the tokenizer of a model.
* `--num-prompts`:
    * Type: `int`
    * Default: `1000`
    * Description: Number of prompts to process.
* `--max-input-length`:
    * Type: `int`
    * Default: `1024`
    * Description: Maximum number of input tokens for filtering the benchmark dataset.
* `--max-output-length`:
    * Type: `int`
    * Default: `1024`
    * Description: Maximum number of output tokens.
* `--request-rate`:
    * Type: `float`
    * Default: `float("inf")`
    * Description: Number of requests per second. If this is inf, then all the requests are sent at time 0. Otherwise, we use Poisson process to synthesize the request arrival times.
* `--save-json-results`:
    * Action: `store_true`
    * Description: Whether to save benchmark results to a json file.
* `--output-bucket`:
    * Type: `str`
    * Default: `None`
    * Description: Specifies the Google Cloud Storage bucket to which JSON-format results will be uploaded. If not provided, no upload will occur.
* `--output-bucket-filepath`:
    * Type: `str`
    * Default: `None`
    * Description: Specifies the destination path within the bucket provided by --output-bucket for uploading the JSON results. This argument requires --output-bucket to be set. If not specified, results will be uploaded to the root of the bucket. If the filepath doesnt exist, it will be created for you.
* `--additional-metadata-metrics-to-save`:
    * Type: `str`
    * Description: Additional metadata about the workload. Should be a dictionary in the form of a string.
* `--scrape-server-metrics`:
    * Action: `store_true`
    * Description: Whether to scrape server metrics.
* `--pm-namespace`:
    * Type: `str`
    * Default: `default`
    * Description: namespace of the pod monitoring object, ignored if scrape-server-metrics is false
* `--pm-job`:
    * Type: `str`
    * Default: `vllm-podmonitoring`
    * Description: name of the pod monitoring object, ignored if scrape-server-metrics is false.