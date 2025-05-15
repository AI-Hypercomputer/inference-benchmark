# Token-Stats & Conversation Extractor

This script loads Hugging Face datasets, extracts "prompt → response" pairs as pretty-printed JSON, and (optionally) computes token-count statistics & histograms.

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
  ```

## Usage
```
python import_dataset.py \
  --count_tokens \
  --tokenizer TOKENIZER \
  --hf_token YOUR_TOKEN \
  --dataset_configs "dataset1,config,prefix,input_col,output_col" "dataset2,config,None,input_col,output_col"
```

* `--tokenizer` – Hugging Face model for AutoTokenizer (default: meta-llama/Llama-3.1-8B-Instruct)
* `--max_samples` – only process up to N examples per dataset (default: 90000)
* `--hf_token` – your Hugging Face CLI token (to access private datasets)
* `--count_tokens` – enable token counting, CSV stats, and histogram plotting
* `--dataset_configs` – dataset configurations in format: "dataset_name,config,input_text_prefix,input_column,output_column"
  * Use "None" for input_text_prefix if none is needed
  * Special escape sequences are supported: "\n" for newline, "\t" for tab, "\r" for carriage return
  * Default: "FiscalNote/billsum,default,Summarize text:\n,text,summary" and "BAAI/Infinity-Instruct,Gen,None,instruction,generation"

## Examples

* Generate conversation JSON with default datasets
```
python import_dataset.py --hf_token YOUR_TOKEN
```

* Compute token stats with a custom tokenizer
```
python import_dataset.py \
  --count_tokens \
  --tokenizer gpt2 \
  --hf_token YOUR_TOKEN
```

* Process custom datasets with special prefixes
```
python import_dataset.py \
  --dataset_configs "openai/webgpt_comparisons,default,Question:\n\n,question,answer" "eli5,eli5-dev,None,input,output" \
  --count_tokens
```

* Use a prefix with a newline (ensure proper escaping)
```
python import_dataset.py \
  --dataset_configs "FiscalNote/billsum,default,Summarize text:\\n,text,summary"
```