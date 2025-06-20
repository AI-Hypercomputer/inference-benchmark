# Copyright 2024 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


r"""Benchmark LLM serving throughput and latency.
This script is for sending requests with prompts to LLM server and benchmark
the latency and throughput at various request rates.

It currently supports TGI, vLLM, Triton TensorRT-LLM and Saxml.
"""

import argparse
import asyncio
from datetime import datetime
import json
import random
import requests
import time
from typing import AsyncGenerator, List, Optional, Tuple, Dict
from prometheus_client import start_http_server, Histogram, Gauge, Counter
import logging

import google.auth
import google.auth.transport.requests
from google.cloud import storage

import aiohttp
import numpy as np
from transformers import AutoTokenizer
from transformers import PreTrainedTokenizerBase

from google.protobuf.timestamp_pb2 import Timestamp

MIN_SEQ_LEN = 4
NEW_TEXT_KEY = "\nOutput:\n"
PROMETHEUS_PORT = 9090

# Prometheus Metrics
prompt_length_metric = Histogram("LatencyProfileGenerator:prompt_length", "Input prompt length", buckets=[2**i for i in range(1, 16)])
response_length_metric = Histogram("LatencyProfileGenerator:response_length", "Response length", buckets=[2**i for i in range(1, 16)])
normalized_time_per_output_token_metric = Histogram('LatencyProfileGenerator:normalized_time_per_output_token_ms', 'Request time over total number of tokens (including first token) (ms)', buckets=[2**i for i in range(1, 16)])
tpot_metric = Histogram('LatencyProfileGenerator:time_per_output_token_ms', 'Time per output token per request (excluding first token) (ms)', buckets=[2**i for i in range(1, 16)])
ttft_metric = Histogram('LatencyProfileGenerator:time_to_first_token_ms', 'Time to first token per request (ms)', buckets=[2**i for i in range(1, 16)])
active_requests_metric = Gauge('LatencyProfileGenerator:active_requests', 'How many requests actively being processed')
total_request_count = Counter('LatencyProfileGenerator:request_count', 'How many total requests have been sent')

# Singleton class to track requests for QPS counting and calculation.
class AsyncRequestCounter:
  _instance = None
  _lock = asyncio.Lock()

  async def __new__(cls, target_requests=None, *args, **kwargs):
    async with cls._lock:
      if not cls._instance:
        cls._instance = super().__new__(cls)
        cls._instance._count = 0
        cls._instance._start_time = time.time()
        cls._instance._target_requests = target_requests
    return cls._instance
  
  async def increment(self):
    async with self._lock:
      self._count += 1
      if self._count == self._target_requests:
        self._end_time = time.time()
  
  async def get_qps(self):
    return self._count / (self._end_time - self._start_time)


# Add trace config for monitoring in flight requests
async def on_request_start(session, trace_config_ctx, params):
    active_requests_metric.inc()
    total_request_count.inc()
    counter = await AsyncRequestCounter()
    await counter.increment()

async def on_request_end(session, trace_config_ctx, params):
    active_requests_metric.dec()

trace_config = aiohttp.TraceConfig()
trace_config.on_request_start.append(on_request_start)
trace_config.on_request_end.append(on_request_end)

# Google Cloud Storage Client
gcs_client = None
gcs_bucket = None

def get_filtered_dataset(
    dataset_path: str,
    max_input_len: int,
    max_output_len: int,
    tokenizer: PreTrainedTokenizerBase,
    use_dummy_text: bool,
) -> List[Tuple[str, int, int]]:
  """Samples requests from the dataset or creates dummy requests."""
  if use_dummy_text:
    dummy_prompt_token_ids = [0] * max_input_len
    dummy_prompt = tokenizer.decode(dummy_prompt_token_ids)
    return [(
          dummy_prompt,
          max_input_len,
          max_output_len,
    )]

  # Load the dataset.
  with open(dataset_path) as f:
    dataset = json.load(f)
  # Filter out the conversations with less than 2 turns.
  dataset = [data for data in dataset if len(data["conversations"]) >= 2]
  # Only keep the first two turns of each conversation.
  dataset = [
      (data["conversations"][0]["value"], data["conversations"][1]["value"])
      for data in dataset
  ]

  # Tokenize the prompts and completions.
  prompts = [prompt for prompt, _ in dataset]
  prompt_token_ids = tokenizer(prompts).input_ids
  completions = [completion for _, completion in dataset]
  completion_token_ids = tokenizer(completions).input_ids
  tokenized_dataset = []
  for i in range(len(dataset)):
    output_len = len(completion_token_ids[i])
    tokenized_dataset.append((prompts[i], prompt_token_ids[i], output_len))

  # Filter out too long sequences.
  filtered_dataset: List[Tuple[str, int, int]] = []
  for prompt, prompt_token_ids, output_len in tokenized_dataset:
    prompt_len = len(prompt_token_ids)
    if prompt_len < MIN_SEQ_LEN or output_len < MIN_SEQ_LEN:
      # Prune too short sequences.
      # This is because TGI causes errors when the input or output length
      # is too short.
      continue
    if prompt_len > max_input_len or output_len > max_output_len:
      # Prune too long sequences.
      continue
    filtered_dataset.append((prompt, prompt_len, output_len))

  return filtered_dataset

async def generate_next_request(
    input_requests: List[Tuple[str, int, int]],
    request_rate: float,
) -> AsyncGenerator[Tuple[str, int, int], None]:
  """Gets request async."""
  while True:
    request = random.choice(input_requests)
    yield request

    if request_rate == float("inf"):
      # If the request rate is infinity, then we don't need to wait.
      continue
    # Sample the request interval from the exponential distribution.
    interval = np.random.exponential(1.0 / request_rate)
    # The next request will be sent after the interval.
    await asyncio.sleep(interval)

def init_errors_map() -> Dict[str, int]:
  errors = {
    "ClientConnectorError": 0,
    "TimeoutError": 0,
    "ContentTypeError": 0,
    "ClientOSError": 0,
    "ServerDisconnectedError": 0,
    "unknown_error": 0,
  }
  return errors

async def send_stream_request(
    backend: str,
    api_url: str,
    prompt: str,
    prompt_len: int,
    output_len: int,
    ignore_eos: bool,
    best_of: int,
    use_beam_search: bool,
    top_k: int,
    tokenizer: PreTrainedTokenizerBase,
    sax_model: str,
    model: str,
    timeout: float,
    max_conn: int,
) -> Tuple[Tuple[int, int, float], float, List[float], Dict[str, int]]:
  """Sends stream request to server"""
  request_start_time_ms = 1000 * time.time()
  errors = init_errors_map()

  headers = {"User-Agent": "Benchmark Client"}
  if backend == "vllm":
    pload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "best_of": best_of,
        "use_beam_search": use_beam_search,
        "temperature": 0.0 if use_beam_search else 1.0,
        "top_p": 1.0,
        "max_tokens": output_len,
        "ignore_eos": ignore_eos,
        "stream": True,
    }
  elif backend == "jetstream":
    pload = {
        "prompt": prompt,
        "max_tokens": output_len,
        "stream": True,
    }
  else: 
    raise ValueError(f"Unknown backend: {backend}")

  ttft_ms = 0.0
  itl_ms = []
  start_time_ms = 1000 * time.perf_counter()
  most_recent_timestamp = start_time_ms
  output = ""
  timeout = aiohttp.ClientTimeout(total=timeout)
  async with aiohttp.ClientSession(timeout=timeout,trust_env=True,connector=aiohttp.TCPConnector(limit=max_conn)) as session:
    try:
      async with session.post(api_url, headers=headers, json=pload, ssl=False) as response:
        async for chunk_bytes in response.content.iter_chunks():
          chunk_bytes = chunk_bytes[0].strip()
          if not chunk_bytes:
              continue
          timestamp_ms = 1000 * time.perf_counter()
          # First token
          if ttft_ms == 0.0:
            ttft_ms = timestamp_ms - start_time_ms
          else:
            itl_ms.append(timestamp_ms - most_recent_timestamp)
          most_recent_timestamp = timestamp_ms
          if backend == "vllm":
            if chunk_bytes.decode("utf-8")[6:] != "[DONE]":
              output += json.loads(chunk_bytes.decode("utf-8")[6:])["choices"][0]["text"]
          elif backend == "jetstream":
            if chunk_bytes.decode("utf-8") != "":
              output += json.loads(chunk_bytes.decode("utf-8"))["text"]
          
    except aiohttp.client_exceptions.ClientConnectorError as client_err:
      errors["ClientConnectorError"] += 1
      print(f"ClientConnectorError: {client_err}")
      return None, None, None, errors
    except asyncio.TimeoutError as timeout_err:
      errors["TimeoutError"] += 1
      print(f"TimeoutError: {timeout_err}")
      return None, None, None, errors
    except aiohttp.client_exceptions.ClientOSError as e:
      errors["ClientOSError"] += 1
      print(f"ClientOSError: {e}")
      return None, None, None, errors
    except aiohttp.client_exceptions.ContentTypeError as e:
      print(f"ContentTypeError: {e}, response: {response}")
      errors["ContentTypeError"] += 1
      return None, None, None, errors
    except aiohttp.client_exceptions.ServerDisconnectedError as e:
      errors["ServerDisconnectedError"] += 1
      print(f"ServerDisconnectedError: {e}")
      return None, None, None, errors
    except Exception as e: 
      print(f"Unknown error {e}")
      errors["unknown_error"] += 1
      return None, None, None, errors
  request_end_time_ms = 1000 * time.time()
  output_token_ids = tokenizer(output).input_ids
  output_len = len(output_token_ids)
  request_latency_ms = (prompt_len, output_len, (request_end_time_ms - request_start_time_ms))

  # Exclude first token for tpot calculation
  if output_len > 1:
    tpot_metric.observe((request_end_time_ms - ttft_ms - request_start_time_ms) / (output_len - 1))
  normalized_time_per_output_token_metric.observe((request_end_time_ms - request_start_time_ms) / output_len)
  if ttft_ms is not None:
    ttft_metric.observe(ttft_ms)
  prompt_length_metric.observe(prompt_len)
  response_length_metric.observe(output_len)
  return request_latency_ms, ttft_ms, itl_ms, None

async def send_request(
    backend: str,
    api_url: str,
    prompt: str,
    prompt_len: int,
    output_len: int,
    ignore_eos: bool,
    best_of: int,
    use_beam_search: bool,
    top_k: int,
    tokenizer: PreTrainedTokenizerBase,
    sax_model: str,
    model: str,
    timeout: float,
    max_conn: int,
) -> Tuple[Tuple[int, int, float], float, List[float], Dict[str, int]]:
  """Sends request to server."""
  request_start_time_ms = 1000 * time.time()
  errors = init_errors_map()

  headers = {"User-Agent": "Benchmark Client"}
  if backend == "vllm":
    pload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "best_of": best_of,
        "use_beam_search": use_beam_search,
        "temperature": 0.0 if use_beam_search else 1.0,
        "top_p": 1.0,
        "max_tokens": output_len,
        "ignore_eos": ignore_eos,
        "stream": False,
    }
  elif backend == "tgi":
    assert not use_beam_search
    params = {
        "best_of": best_of,
        "max_new_tokens": output_len,
        "do_sample": True,
    }
    pload = {
        "inputs": prompt,
        "parameters": params,
    }
  elif backend == "naive_transformers":
    # If max_length or top_k is not specified _MAX_LENGTH_DEFAULT = 200 and
    # _TOP_K_DEFAULT = 10 in peft/handler.py will be used.
    pload = {
        "instances": [{
            "prompt": prompt,
            "max_length": output_len,
            "top_k": top_k,
        }]
    }
  elif backend == "tensorrt_llm_triton":
    pload = {
        "text_input": prompt,
        "max_tokens": output_len,
        "beam_width": 1 if not use_beam_search else best_of,
        "temperature": 0.0 if use_beam_search else 1.0,
        "top_p": 1.0,
        "bad_words": "",
        "stop_words": "",
        "stream": False,
    }
  elif backend == "sax":
    pload = {
        "model": sax_model,
        "prompt": prompt,
        "n": 1,
        "best_of": best_of,
        "use_beam_search": use_beam_search,
        "temperature": 0.0 if use_beam_search else 1.0,
        "top_p": 1.0,
        "top_k": 50,
        "max_tokens": output_len,
        "stream": False,
    }
  elif backend == "jetstream":
    pload = {
        "prompt": prompt,
        "max_tokens": output_len,
    }
  else:
    raise ValueError(f"Unknown backend: {backend}")

  # Set client timeout to be 3 hrs.
  timeout = aiohttp.ClientTimeout(total=timeout)
  async with aiohttp.ClientSession(timeout=timeout,trust_env=True,trace_configs=[trace_config],connector=aiohttp.TCPConnector(limit=max_conn)) as session:
    while True:
      try:
        async with session.post(api_url, headers=headers, json=pload, ssl=False) as response:
          output = await response.json()

        # Re-send the request if it failed.
        if "error" not in output:
          break
      except aiohttp.client_exceptions.ClientConnectorError as client_err:
        errors["ClientConnectorError"] += 1
        print(f"ClientConnectorError: {client_err}")
        return None, None, None, errors
      except asyncio.TimeoutError as timeout_err:
        errors["TimeoutError"] += 1
        print(f"TimeoutError: {timeout_err}")
        return None, None, None, errors
      except aiohttp.client_exceptions.ClientOSError as e:
        errors["ClientOSError"] += 1
        print(f"ClientOSError: {e}")
        return None, None, None, errors
      except aiohttp.client_exceptions.ContentTypeError as e:
        print(f"ContentTypeError: {e}, response: {response}")
        errors["ContentTypeError"] += 1
        return None, None, None, errors
      except aiohttp.client_exceptions.ServerDisconnectedError as e:
        errors["ServerDisconnectedError"] += 1
        print(f"ServerDisconnectedError: {e}")
        return None, None, None, errors
      except Exception as e: 
        print(f"Unknown error {e}")
        errors["unknown_error"] += 1
        return None, None, None, errors

  request_end_time_ms = 1000 * time.time()
  # Naive HF transformers generation and TensorRT-LLM generation stops at EOS
  # tokens and the generation may be shorter than the ground-truth output
  # sequence length.
  if backend == "naive_transformers":
    complete_pred = output["predictions"][0][0]["generated_text"]
    new_text_start_index = complete_pred.find(NEW_TEXT_KEY) + len(NEW_TEXT_KEY)
    pred = complete_pred[new_text_start_index:]
    output_token_ids = tokenizer(pred).input_ids
    output_len = len(output_token_ids) - prompt_len
  elif backend == "tensorrt_llm_triton":
    output_token_ids = tokenizer(output["text_output"]).input_ids
    output_len = len(output_token_ids)
  elif backend == "sax":
    output_token_ids = tokenizer(output["choices"][0]["text"]).input_ids
    output_len = len(output_token_ids)
  elif backend == "tgi":
    output_token_ids = tokenizer(output["generated_text"]).input_ids
    output_len = len(output_token_ids)
  elif backend == "vllm":
    output_token_ids = tokenizer(output["choices"][0]["text"]).input_ids
    output_len = len(output_token_ids)
  elif backend == "jetstream":
    output_token_ids = tokenizer(output["response"]).input_ids
    output_len = len(output_token_ids)

  # (prompt len, output len, latency, success)
  request_latency_ms = (prompt_len, output_len, (request_end_time_ms - request_start_time_ms))
  normalized_time_per_output_token_metric.observe((request_end_time_ms - request_start_time_ms) / output_len)
  prompt_length_metric.observe(prompt_len)
  response_length_metric.observe(output_len)

  return request_latency_ms, None, None, None


async def run_single_request(args: argparse.Namespace, api_url: str, tokenizer: PreTrainedTokenizerBase,
                               prompt: str, prompt_len: int, output_len: int, chosen_model: str) -> Tuple[str, Tuple]:
    if args.stream_request:
        result = await send_stream_request(
            args.backend, api_url, prompt, prompt_len, output_len, args.ignore_eos,
            args.best_of, args.use_beam_search, args.top_k, tokenizer, args.sax_model,
            chosen_model, args.request_timeout, args.tcp_conn_limit)
    else:
        result = await send_request(
            args.backend, api_url, prompt, prompt_len, output_len, args.ignore_eos,
            args.best_of, args.use_beam_search, args.top_k, tokenizer, args.sax_model,
            chosen_model, args.request_timeout, args.tcp_conn_limit)
    return chosen_model, result

async def benchmark(
    args: argparse.Namespace, 
    api_url: str,
    tokenizer: PreTrainedTokenizerBase,
    models: List[str],
    traffic_split: List[float],
) -> None:
    """Runs benchmark requests with model selection per request based on weighted ratio.
    Also saves results separately for each model.
    """
    input_requests = get_filtered_dataset(
        args.dataset, args.max_input_length, args.max_output_length, tokenizer, args.use_dummy_text)
    
    # Combine the models list and traffic split list into a dict

    
    if traffic_split is None:
      traffic_split = [1.0 / len(models)] * len(models)
    if len(models) != len(traffic_split):
        raise ValueError("The number of models and traffic split values must match")
    total_weight = sum(traffic_split)
    if abs(total_weight - 1.0) > 1e-6:
        raise ValueError(f"Traffic split must sum to 1.0, but got {total_weight}")
    models_dict = dict(zip(models, traffic_split))
    model_names = list(models_dict.keys())
    model_weights = list(models_dict.values())

    benchmark_start_time_sec = time.time()
    # Initialize the counter with target prompts
    await AsyncRequestCounter(args.num_prompts)
    tasks: List[asyncio.Task] = []
    prompts_sent = 0
    async for request in generate_next_request(input_requests, args.request_rate):
        if prompts_sent >= args.num_prompts:
            break
        prompt, prompt_len, output_len = request
        chosen_model = random.choices(model_names, weights=model_weights)[0]
        task = asyncio.create_task(run_single_request(args, api_url, tokenizer, prompt, prompt_len, output_len, chosen_model))
        tasks.append(task)
        prompts_sent += 1

    results = await asyncio.gather(*tasks)

    overall_results = {"latencies": [], "ttfts": [], "itls": [], "tpots": [], "errors": init_errors_map()}
    per_model_results: Dict[str, Dict[str, List]] = {}
    for model in model_names:
        per_model_results[model] = {"latencies": [], "ttfts": [], "itls": [], "tpots": [], "errors": init_errors_map()}

    for chosen_model, res in results:
        if res is None:
            continue
        latency, ttft_ms, itl_ms, errors = res
        if errors:
          for k, v in errors.items():
              overall_results["errors"][k] += v
              per_model_results[chosen_model]["errors"][k] += v
        else:
          prompt_len, output_len, request_latency_ms = latency
          overall_results["latencies"].append(latency)
          per_model_results[chosen_model]["latencies"].append(latency)
          if ttft_ms:
              overall_results["ttfts"].append(ttft_ms)
              overall_results["tpots"].append((request_latency_ms - ttft_ms) / (output_len - 1) if output_len > 1 else 0)
              per_model_results[chosen_model]["ttfts"].append(ttft_ms)
              per_model_results[chosen_model]["tpots"].append((request_latency_ms - ttft_ms) / (output_len - 1) if output_len > 1 else 0)
          if itl_ms:
              overall_results["itls"].extend(itl_ms)     
              per_model_results[chosen_model]["itls"].extend(itl_ms)     

    benchmark_duration_sec = time.time() - benchmark_start_time_sec
    
    await print_and_save_result(args, benchmark_duration_sec, prompts_sent, "weighted",
                          overall_results["latencies"], overall_results["ttfts"],
                          overall_results["itls"], overall_results["tpots"],
                          overall_results["errors"])
    for model, data in per_model_results.items():
        await print_and_save_result(args, benchmark_duration_sec, len(data["latencies"]), model,
                              data["latencies"], data["ttfts"], data["itls"],
                              data["tpots"], data["errors"])

def save_json_results(args: argparse.Namespace, benchmark_result, server_metrics, model, errors):
  # Setup
  start_dt_proto = Timestamp()
  start_dt_proto.FromDatetime(args.start_datetime)

  final_json = {
    # metrics values are numerical
    "metrics" : {
      # Traffic
      "num_prompts_attempted": benchmark_result['num_prompts_attempted'],
      "num_prompts_succeeded": benchmark_result['num_prompts_succeeded'],
      "request_rate": args.request_rate,
      "queries_per_second": benchmark_result['queries_per_second'],
      'server_metrics': {
        **server_metrics
      },
      **benchmark_result,
      **errors,
    },
    # dimensions values are strings
    "dimensions": {
      "date": args.start_datetime.strftime('%Y%m%d-%H%M%S'),
      "backend": args.backend,
      "model_id": model,
      "tokenizer_id": args.tokenizer,
      **(json.loads(args.additional_metadata_metrics_to_save) if args.additional_metadata_metrics_to_save else {})
    },
    "config": {
      "model": model,
      "num_models": len(args.models.split(',')),
      "model_server": args.backend,
      "start_time": {
        "seconds" : start_dt_proto.seconds,
        "nanos" : start_dt_proto.nanos
      }
    },
    "summary_stats": {
      "stats": [{
        "request_rate": args.request_rate,
        "request_latency": {
          "mean": benchmark_result["avg_latency_ms"],
          "median": benchmark_result["median_latency_ms"],
          "sd": benchmark_result["sd_latency_ms"],
          "min": benchmark_result["min_latency_ms"],
          "max": benchmark_result["max_latency_ms"],
          "p90": benchmark_result["p90_latency_ms"],
          "p99": benchmark_result["p99_latency_ms"],
        },
        "throughput": {
          "mean": benchmark_result['throughput']
        },
        "input_length": {
          "mean": benchmark_result["avg_input_len"],
          "median": benchmark_result["median_input_len"],
          "sd": benchmark_result["sd_input_len"],
          "min": benchmark_result["min_input_len"],
          "max": benchmark_result["max_input_len"],
          "p90": benchmark_result["p90_input_len"],
          "p99": benchmark_result["p99_input_len"],
        },
        "output_length": {
          "mean": benchmark_result["avg_output_len"],
          "median": benchmark_result["median_output_len"],
          "sd": benchmark_result["sd_output_len"],
          "min": benchmark_result["min_output_len"],
          "max": benchmark_result["max_output_len"],
          "p90": benchmark_result["p90_output_len"],
          "p99": benchmark_result["p99_output_len"],
        },
        "tpot": {
          "mean": benchmark_result["avg_normalized_time_per_output_token_ms"],
          "median": benchmark_result["median_normalized_time_per_output_token_ms"],
          "sd": benchmark_result["sd_normalized_time_per_output_token_ms"],
          "min": benchmark_result["min_normalized_time_per_output_token_ms"],
          "max": benchmark_result["max_normalized_time_per_output_token_ms"],
          "p90": benchmark_result["p90_normalized_time_per_output_token_ms"],
          "p99": benchmark_result["p99_normalized_time_per_output_token_ms"],
        },
        "model_server_metrics" : [{"Name": name, **metrics} for name, metrics in server_metrics.items()]
      }]
    }
  }
  
  # Save to file
  model_without_slash = model.replace("/","-")
  file_name = (
      f"{args.file_prefix}-{args.backend}-{args.request_rate}qps-{args.start_datetime.strftime('%Y%m%d-%H%M%S')}-{model_without_slash}.json"
  )
  with open(file_name, "w", encoding="utf-8") as outfile:
    json.dump(final_json, outfile)
  if gcs_bucket is not None:
    try:
      gcs_bucket.blob(f"{args.output_bucket_filepath}/{file_name}").upload_from_filename(file_name)
      print(f"File {file_name} uploaded to gs://{args.output_bucket}/{args.output_bucket_filepath}")
    except google.cloud.exceptions.NotFound:
      print(f"GS Bucket (gs://{args.output_bucket}) does not exist")

def metrics_to_scrape(backend: str) -> List[str]:
  # Each key in the map is a metric, it has a corresponding 'stats' object
  # It must be populated on the outputs 'metrics' field as 'key':'stats'
  # If a value is specified for a given key, it will be populated on the outputs `summary_stats.stats` field as 'value':'stats' as well.
  if backend == "vllm":
    return [
      "vllm:cpu_cache_usage_perc",
      "vllm:gpu_cache_usage_perc",

      "vllm:num_requests_waiting",
      "vllm:num_requests_running",
      "vllm:num_requests_swapped",

      "vllm:time_to_first_token_seconds",
      "vllm:time_per_output_token_seconds",
      "vllm:e2e_request_latency_seconds",

      "vllm:request_prefill_time_seconds",
      "vllm:request_queue_time_seconds",
      "vllm:request_decode_time_seconds",
      "vllm:request_inference_time_seconds",
      "vllm:time_in_queue_requests",

      "vllm:request_prompt_tokens",
      "vllm:request_generation_tokens",
      "vllm:iteration_tokens_total",
      "vllm:prompt_tokens_total",
      "vllm:generation_tokens_total",
      "vllm:request_success_total",
      "vllm:num_preemptions_total",

      "vllm:cpu_prefix_cache_hit_rate",
      "vllm:gpu_prefix_cache_hit_rate",

      "vllm:avg_generation_throughput_toks_per_s",
      "vllm:avg_prompt_throughput_toks_per_s",
    ]
  elif backend == "jetstream":
    return [
      "jetstream_slots_used_percentage",
      "jetstream_prefill_backlog_size",
    ]
  else:
    return []

def print_metrics(metrics: List[str], duration_sec: float, namespace: str, job: str):
  # Creates a credentials object from the default service account file
  # Assumes that script has appropriate default credentials set up, ref:
  # https://googleapis.dev/python/google-auth/latest/user-guide.html#application-default-credentials
  credentials, project_id = google.auth.default()
  # Prepare an authentication request - helps format the request auth token
  auth_req = google.auth.transport.requests.Request()

  server_metrics = {}

  # Request refresh tokens
  credentials.refresh(auth_req)
  url='https://monitoring.googleapis.com/v1/projects/%s/location/global/prometheus/api/v1/metadata' % (project_id)
  headers_api = {'Authorization': 'Bearer ' + credentials.token}
  request_post = requests.get(url=url, headers=headers_api)
  all_metrics_metadata = request_post.json()
  if request_post.ok is not True:
    print("HTTP Error: %s" % (all_metrics_metadata))
    return server_metrics
  if all_metrics_metadata["status"] != "success":
    print("Metadata error response: %s" % all_metrics_metadata["error"])
    return server_metrics

  for metric in metrics:
    # Find metric type
    if metric not in all_metrics_metadata['data']:
      logger.debug(f"No metric found for {metric}")
      continue
    metric_type = all_metrics_metadata['data'][metric]
    metric_type = metric_type[0]['type']

    metric_results = {}
    # Queries scrape all metrics collected from the last $DURATION seconds from the backend's related
    # podmonitoring spec assumed to be named "$BACKEND-podmonitoring"

    filters = ""
    if job != "":
        filters += f'job="{job}"'
    if namespace != "":
        if filters != "":
            filters += ","
        filters += f'namespace="{namespace}"'
    if filters != "":
        filters = f"{{{filters}}}"

    queries = {
        "gauge": {
            "Mean": f"avg_over_time({metric}{filters}[{duration_sec:.0f}s])",
            "Median": f"quantile_over_time(0.5, {metric}{filters}[{duration_sec:.0f}s])",
            "Sd": f"stddev_over_time({metric}{filters}[{duration_sec:.0f}s])",
            "Min": f"min_over_time({metric}{filters}[{duration_sec:.0f}s])",
            "Max": f"max_over_time({metric}{filters}[{duration_sec:.0f}s])",
            "P90": f"quantile_over_time(0.9, {metric}{filters}[{duration_sec:.0f}s])",
            "P95": f"quantile_over_time(0.95, {metric}{filters}[{duration_sec:.0f}s])",
            "P99": f"quantile_over_time(0.99, {metric}{filters}[{duration_sec:.0f}s])",
        },
        "histogram": {
            "Mean": f"sum(rate({metric}_sum{filters}[{duration_sec:.0f}s])) / sum(rate({metric}_count{filters}[{duration_sec:.0f}s]))",
            "Median": f"histogram_quantile(0.5, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
            "Min": f"histogram_quantile(0, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
            "Max": f"histogram_quantile(1, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
            "P90": f"histogram_quantile(0.9, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
            "P95": f"histogram_quantile(0.95, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
            "P99": f"histogram_quantile(0.99, sum(rate({metric}_bucket{filters}[{duration_sec:.0f}s])) by (le))",
        },
        "counter": {
            "Sum": f"sum_over_time({metric}{filters}[{duration_sec:.0f}s])",
            "Rate": f"rate({metric}{filters}[{duration_sec:.0f}s])",
            "Increase": f"increase({metric}{filters}[{duration_sec:.0f}s])",
            "Mean": f"avg_over_time(rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
            "Max": f"max_over_time(rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
            "Min": f"min_over_time(rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
            "P90": f"quantile_over_time(0.9, rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
            "P95": f"quantile_over_time(0.95, rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
            "P99": f"quantile_over_time(0.99, rate({metric}{filters}[{duration_sec:.0f}s])[{duration_sec:.0f}s:{duration_sec:.0f}s])",
        },
    }

    for query_name, query in queries[metric_type].items():
      # Configure respective query
      url='https://monitoring.googleapis.com/v1/projects/%s/location/global/prometheus/api/v1/query' % (project_id)
      headers_api = {'Authorization': 'Bearer ' + credentials.token}
      params = {'query': query}
      logger.debug(f"Finding {query_name} {metric} with the following query: {query}")
      request_post = requests.get(url=url, headers=headers_api, params=params)
      response = request_post.json()

      logger.debug(f"Got response from metrics server: {response}")

      # handle response
      if request_post.ok:
        if response["status"] == "success" and response["data"] and response["data"]["result"]:
          r = response["data"]["result"]
          if not r:
            logger.debug(f"Failed to get result for {query_name}")
            continue
          v = r[0].get("value", None)
          if not v:
            logger.debug(f"Failed to get value for result: {r}")
            continue
          metric_results[query_name] = float(v[1])
          logger.debug("%s: %s" % (query_name, v[1]))
        else:
          logger.debug("Cloud Monitoring PromQL Error: %s" % (response))
          continue
      else:
        logger.debug("HTTP Error: %s" % (response))
        continue
    server_metrics[metric] = metric_results
  
  return server_metrics

def get_stats_for_set(name, description, points):
  avg = np.mean(points) if points else 0
  median = np.median(points) if points else 0
  sd = np.std(points) if points else 0
  min = np.min(points) if points else 0
  max = np.max(points) if points else 0
  p90 = np.percentile(points, 90) if points else 0
  p99 = np.percentile(points, 99) if points else 0

  print(f"Average {description}:" f" {avg:.2f}")

  return {
    f'avg_{name}':  avg,
    f'median_{name}': median,
    f'sd_{name}': sd,
    f'min_{name}': min,
    f'max_{name}': max,
    f'p90_{name}': p90,
    f'p99_{name}': p99,
  }

async def print_and_save_result(args: argparse.Namespace, benchmark_duration_sec, total_requests, model, request_latencies, ttfts, itls, tpots, errors):
  benchmark_result = {}

  print(f"====Result for Model: {model}====")
  print(f"Errors: {errors}")
  print(f"Total time (seconds): {benchmark_duration_sec:.2f} s")
  print(f"Successful/total requests: {len(request_latencies)}/{total_requests}")
  print(f"Requests/sec: {total_requests / benchmark_duration_sec:.2f}")
  counter = await AsyncRequestCounter()
  queries_per_second = await counter.get_qps()
  print(f"Queries/sec: {queries_per_second:.2f}")
  benchmark_result['queries_per_second'] = queries_per_second
  benchmark_result["num_prompts_attempted"] = total_requests
  benchmark_result["num_prompts_succeeded"] = len(request_latencies)
  benchmark_result['benchmark_time'] = benchmark_duration_sec
  benchmark_result['throughput_rps'] = (args.num_prompts / benchmark_duration_sec)

  total_output_tokens = np.sum([output_len for _, output_len, _ in
                                request_latencies])
  output_tokens_per_second = total_output_tokens / benchmark_duration_sec
  benchmark_result['throughput'] = output_tokens_per_second

  print(f"Output_tokens/sec: {output_tokens_per_second:.2f}")
  benchmark_result['total_output_token'] = int(total_output_tokens)

  total_input_tokens = np.sum([prompt_len for prompt_len, _, _ in
                               request_latencies])
  input_tokens_per_sec = total_input_tokens / benchmark_duration_sec
  print(f"Input_tokens/sec: {input_tokens_per_sec:.2f}")
  benchmark_result['total_input_tokens'] = int(total_input_tokens)
  benchmark_result['input_tokens_per_sec'] = input_tokens_per_sec

  total_tokens = total_input_tokens + total_output_tokens
  tokens_per_sec = total_tokens / benchmark_duration_sec
  print(f"Tokens/sec: {tokens_per_sec:.2f}")
  benchmark_result['total_tokens'] = int(total_tokens)
  benchmark_result['tokens_per_sec'] = tokens_per_sec
  ttft_stats = {}
  itls_stats = {}
  tpot_stats = {}
  if args.stream_request:
    ttft_stats = get_stats_for_set("TTFT_ms", "Time to First Token (ms)", ttfts)
    itls_stats = get_stats_for_set("ITL_ms", "Inter-Token Latency (ms)", itls)
    tpot_stats = get_stats_for_set("TPOT_ms", "Time Per Output Token (ms)", tpots)
  if args.machine_cost:
    print(
        "Cost $/1k tokens:"
        f" {args.machine_cost * 1000 / output_tokens_per_second}"
    )

  benchmark_result = {
    **benchmark_result,
    **(get_stats_for_set("per_token_latency_ms", "milliseconds/token (includes waiting time on server)", [
      latency / (prompt_len + output_len)
      for prompt_len, output_len, latency in request_latencies
    ])),
    **ttft_stats,
    **itls_stats,
    # NOTE: The latency below includes requests awaiting time on server side.
    # It's not comparable with the model inference latency for batch size 1.
    **(get_stats_for_set("latency_ms", "milliseconds/request (includes waiting time on server)" ,[latency for _, _, latency in request_latencies])),
    **(get_stats_for_set("normalized_time_per_output_token_ms", "milliseconds/output_token (includes waiting time on server)", [latency / output_len for _, output_len, latency in request_latencies])),
    **(get_stats_for_set("input_len", "input length", [float(prompt_len) for prompt_len, _, _ in request_latencies])),
    **(get_stats_for_set("output_len", "output length", [float(output_len) for _, output_len, _ in request_latencies]))
  }

  server_metrics = {}
  if args.scrape_server_metrics:
    server_metrics = print_metrics(metrics_to_scrape(args.backend), benchmark_duration_sec, args.pm_namespace, args.pm_job)
  if args.save_json_results:
    save_json_results(args, benchmark_result, server_metrics, model, errors)

async def main(args: argparse.Namespace):
  print(args)
  models = args.models.split(',')
  print(f"Models to benchmark: {models}")
  if args.traffic_split:
    print(f"Traffic split: {args.traffic_split}")
  else:
    print("No traffic split specified. Defaulting to uniform traffic split.")
  random.seed(args.seed)
  np.random.seed(args.seed)
  endpoint = (
    "v1/completions"
    if args.backend == "vllm"
    else args.endpoint
)
  
  # Create GCS client before benchmarking
  # Should fail fast if client is misconfigured or missing permissions
  if args.output_bucket is not None:
    global gcs_client
    gcs_client = storage.Client()
    global gcs_bucket
    gcs_bucket = gcs_client.bucket(args.output_bucket)

    if args.output_bucket_filepath:
      blob = gcs_bucket.blob(args.output_bucket_filepath)
      if not blob.exists():
        blob.upload_from_string('')

  print(f"Starting Prometheus Server on port {PROMETHEUS_PORT}")
  start_http_server(PROMETHEUS_PORT)

  api_url = f"http://{args.host}:{args.port}/{endpoint}"
  tokenizer = AutoTokenizer.from_pretrained(
      args.tokenizer, trust_remote_code=args.trust_remote_code
  )

  benchmark_start_time = time.time()
  args.start_datetime = datetime.fromtimestamp(benchmark_start_time)
  
  await benchmark(args, api_url, tokenizer,models, args.traffic_split)
  



def parse_traffic_split(arg):
    try:
        return [float(x) for x in arg.split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Traffic split must be a comma-separated list of floats, e.g. '0.9,0.1'"
        )

if __name__ == "__main__":
  parser = argparse.ArgumentParser(
      description="Benchmark the online serving throughput."
  )
  parser.add_argument(
      "--backend",
      type=str,
      default="vllm",
      choices=[
          "vllm",
          "tgi",
          "naive_transformers",
          "tensorrt_llm_triton",
          "sax",
          "jetstream"
      ],
  )
  parser.add_argument(
      "--sax_model",
      type=str,
      default="",
      help="Model name to send request to at API server for SAX model server.",
  )
  parser.add_argument("--file-prefix", type=str, default="benchmark")
  parser.add_argument("--endpoint", type=str, default="generate")
  parser.add_argument("--host", type=str, default="localhost")
  parser.add_argument("--port", type=int, default=7080)
  parser.add_argument("--dataset", type=str, help="Path to the dataset.")
  parser.add_argument(
    "--models",
    type=str,
    help="Comma separated list of models to benchmark.",
  )
  parser.add_argument(
    "--traffic-split",
    type=parse_traffic_split,
    default=None,
    help="Comma-separated list of traffic split proportions for the models, e.g. '0.9,0.1'. Sum must equal 1.0."
)
  parser.add_argument(
    "--stream-request", 
    action="store_true",
    help="Whether to stream the request. Needed for TTFT metric",
  )
  parser.add_argument(
    "--request-timeout", 
    type=float,
    default=(3.0 * 60.0 * 60.0),
    help="Individual request timeout",
  )
  parser.add_argument(
      "--tokenizer",
      type=str,
      required=True,
      help="Name or path of the tokenizer.",
  )
  parser.add_argument(
      "--best-of",
      type=int,
      default=1,
      help="Generates `best_of` sequences per prompt and returns the best one.",
  )
  parser.add_argument("--use-beam-search", action="store_true")
  parser.add_argument(
      "--num-prompts",
      type=int,
      default=1000,
      help="Number of prompts to process.",
  )
  parser.add_argument(
      "--max-input-length",
      type=int,
      default=1024,
      help=(
          "Maximum number of input tokens for filtering the benchmark dataset."
      ),
  )
  parser.add_argument(
      "--max-output-length",
      type=int,
      default=1024,
      help=(
          "Maximum number of input tokens for filtering the benchmark dataset."
      ),
  )
  parser.add_argument(
    "--ignore-eos",
    action="store_true",
    help=(
        "If set and model server is vllm, the generation process will ignore the end-of-sequence (EOS) token, "
        "allowing output to continue until reaching --max-output-length or another stopping condition."
    ),
  )
  parser.add_argument(
      "--top-k",
      type=int,
      default=32000,
      help=(
          "Number of candidate tokens that are considered at each step of the"
          " generation process. 32000 is the vocab_size of Open-LLaMA and"
          " LLaMA2 models."
      ),
  )
  parser.add_argument(
      "--request-rate",
      type=float,
      default=float("inf"),
      help=(
          "Number of requests per second. If this is inf, "
          "then all the requests are sent at time 0. "
          "Otherwise, we use Poisson process to synthesize "
          "the request arrival times."
      ),
  )
  parser.add_argument("--seed", type=int, default=int(time.time()))
  parser.add_argument(
      "--trust-remote-code",
      action="store_true",
      help="trust remote code from huggingface",
  )
  parser.add_argument(
      "--machine-cost",
      type=float,
      default=None,
      help="Machine cost per hour including accelerators (if any)",
  )
  parser.add_argument(
      "--use-dummy-text",
      action="store_true",
      help=(
          "Whether to use dummy text with length defined by max_input_length"
          " and max_output_length."
      ),
  )
  parser.add_argument(
      "--save-json-results",
      action="store_true",
      help="Whether to save benchmark results to a json file.",
  )
  parser.add_argument(
    "--output-bucket",
    type=str,
    default=None,
    help=(
      "Specifies the Google Cloud Storage bucket to which JSON-format results"
      " will be uploaded. If not provided, no upload will occur."
    )
  )
  parser.add_argument(
    "--output-bucket-filepath",
    type=str,
    default=None,
    help=(
      "Specifies the destination path within the bucket provided by"
      " --output-bucket for uploading the JSON results. This argument requires"
      " --output-bucket to be set. If not specified, results will be uploaded "
      " to the root of the bucket. If the filepath doesnt exist, it will be"
      " created for you."
    )
  )
  parser.add_argument(
    "--save-aggregated-result",
    action="store_true",
    help="Whether to aggregate results of all models and save the result.",
  )
  parser.add_argument(
      "--additional-metadata-metrics-to-save",
      type=str,
      help=(
          "Additional metadata about the workload. Should be a dictionary in"
          " the form of a string."
      ),
  )
  parser.add_argument(
      "--scrape-server-metrics",
      action="store_true",
      help="Whether to scrape server metrics.",
  )
  parser.add_argument("--pm-namespace", type=str, default="default", help="namespace of the pod monitoring object, ignored if scrape-server-metrics is false")
  parser.add_argument("--pm-job", type=str, default="vllm-podmonitoring", help="name of the pod monitoring object, ignored if scrape-server-metrics is false")
  parser.add_argument("--tcp-conn-limit", type=int, default=100, help="Max number of tcp connections allowed per aiohttp ClientSession")
  cmd_args = parser.parse_args()
  
  level = logging.INFO
  logger = logging.getLogger(__name__)
  logger.setLevel(level)
  handler = logging.StreamHandler()  # This sends output to the console
  handler.setLevel(level) # Set handler level
  logger.addHandler(handler)
  
  asyncio.run(main(cmd_args))