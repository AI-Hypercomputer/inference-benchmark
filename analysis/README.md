# Analyze the results

To analyze the results, use the following command to chart the latency vs throughput curve based on the json files in the results folder.

```
pip install matplotlib
python analyze.py results/
```

To analyze the price-perf results, you can include instance pricing to get a chart with $ per million output tokens in addition.

```
python analyze.py results/ --instance-price-per-hour 10
```

To analyze results across different runs and compare their latency and throughput metrics, you can do the following:

```
python analyze.py run-1/ run-2/ run-3/
```