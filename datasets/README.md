# Token-Stats & Conversation Extractor

This script loads several Hugging Face datasets, extracts “prompt → response” pairs as pretty-printed JSON, and (optionally) computes token-count statistics & histograms.

---

## Features

- **Conversation JSON**  
  Builds `<dataset>_conversations.json`, containing arrays of `{from: "human", value: …}` / `{from: "gpt", value: …}`.  
- **Token counts & stats** (with `--count_tokens`)  
  - `<dataset>_token_counts.csv`: per-example output token count  
  - `<dataset>_token_stats.csv`: summary (mean, median, std, min, max) for input, output, and total tokens  
  - `<dataset>_token_distributions.png`: histograms for input, output, and total token distributions  

---

## Prerequisites

- Python 3.7+  
- Install dependencies:
  ```bash
  pip install datasets transformers numpy pandas tqdm matplotlib


## Usage
```
python analyze_tokens.py \
  --count_tokens \
  --tokenizer TOKENIZER \
  --hf_token YOUR_TOKEN
```

* `--tokenizer` – Hugging Face model for AutoTokenizer (default: meta-llama/Llama-3.1-8B-Instruct)
* `--max_samples` – only process up to N examples per dataset (default: 90000)
* `--hf_token` – your Hugging Face CLI token (to access private datasets)
* `--count_tokens` – enable token counting, CSV stats, and histogram plotting

## Examples
* Generate conversation JSON only

```
python analyze_tokens.py --hf_token YOUR_TOKEN
```

* Also compute token stats with a tokenizer
```
python analyze_tokens.py \
  --count_tokens \
  --tokenizer TOKENIZER \
  --hf_token YOUR_TOKEN
```