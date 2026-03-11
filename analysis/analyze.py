import json
import matplotlib.pyplot as plt
import os
import argparse

def _create_line_plot(all_series_data, x_label, y_label, title, output_filename_base):
    """
    Helper function to generate and save a line plot with multiple series.

    Args:
        all_series_data (dict): A dict where key is the series label (e.g., folder name)
                                and value is a dict {'x': [...], 'y': [...]}.
        x_label (str): Label for the x-axis.
        y_label (str): Label for the y-axis.
        title (str): Title of the plot.
        output_filename_base (str): Base name for the output PNG file.
    """
    if not all_series_data:
        print(f"No valid data for '{title}'. Cannot generate plot.")
        return

    plt.figure(figsize=(12, 7))

    for series_label, series_data in all_series_data.items():
        x_data = series_data.get('x')
        y_data = series_data.get('y')

        if not x_data or not y_data:
            print(f"Warning: Skipping series '{series_label}' for plot '{title}' due to missing data.")
            continue

        # To draw a clean line, sort the points based on the x-axis value.
        sorted_points = sorted(zip(x_data, y_data))
        if not sorted_points:
            continue
        x_sorted, y_sorted = zip(*sorted_points)

        plt.plot(x_sorted, y_sorted, marker='o', linestyle='-', label=series_label)

    plt.title(title)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    plt.legend()
    plt.grid(True)
    output_filename = f'{output_filename_base}.png'
    plt.savefig(output_filename)
    print(f"Chart saved to {output_filename}")
    plt.show()
    plt.close()

def _print_summary(parsed_data_points):
    """Prints a summary of the best performing data points."""
    if not parsed_data_points:
        return

    print("\n--- Benchmark Summary ---")

    # --- Find Max Throughput and Best Price/Performance---
    # Filter out points that might be missing the throughput key, though current logic prevents this.
    valid_throughput_points = [p for p in parsed_data_points if p.get("throughput") is not None]
    if valid_throughput_points:
        max_throughput_point = max(valid_throughput_points, key=lambda p: p["throughput"])
        print("\n🏆 Best Throughput / Best Price/Performance:")
        print(f"  - File: {max_throughput_point['filename']}")
        print(f"  - Throughput: {max_throughput_point['throughput']:.2f} tokens/sec")
        print(f"  - Request Rate: {max_throughput_point['request_rate']} qps")
        if max_throughput_point.get('latency') is not None:
            print(f"  - Avg Per Token Latency: {max_throughput_point['latency']:.2f} ms")
        if max_throughput_point.get('normalized_latency') is not None:
            print(f"  - Avg Normalized Latency: {max_throughput_point['normalized_latency']:.2f} ms")
        if max_throughput_point.get('p90_normalized_latency') is not None:
            print(f"  - P90 Normalized Latency: {max_throughput_point['p90_normalized_latency']:.2f} ms")
        if max_throughput_point.get('cost_per_million_tokens') is not None:
            print(f"  - Cost: ${max_throughput_point['cost_per_million_tokens']:.2f} per million tokens")

    print("\n-----------------------\n")

def _parse_folder_data(folder_path, instance_price_per_hour):
    """Parses all JSON benchmark files in a single folder."""
    parsed_data_points = []
    print(f"Scanning folder: {folder_path}")

    if not os.path.isdir(folder_path):
        print(f"Error: The provided path '{folder_path}' is not a valid directory.")
        return parsed_data_points

    for filename in os.listdir(folder_path):
        if filename.lower().endswith('.json'):
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)

                metrics = data.get("metrics", {})
                throughput = metrics.get("throughput")
                latency = metrics.get("avg_per_token_latency_ms")
                normalized_latency = metrics.get("avg_normalized_time_per_output_token_ms")
                p90_normalized_latency = metrics.get("p90_normalized_time_per_output_token_ms")
                request_rate = metrics.get("request_rate")

                if throughput is not None and request_rate is not None:
                    point_data = {
                        "throughput": throughput,
                        "latency": latency,
                        "normalized_latency": normalized_latency,
                        "p90_normalized_latency": p90_normalized_latency,
                        "request_rate": request_rate,
                        "filename": filename
                    }
                    # Calculate cost per million tokens if price is provided and throughput is valid
                    if instance_price_per_hour is not None and throughput > 0:
                        # Cost = (Price/hour * 1,000,000 tokens) / (tokens/sec * 3600 sec/hour)
                        cost_per_million_tokens = (instance_price_per_hour * 1000000) / (throughput * 3600)
                        point_data["cost_per_million_tokens"] = cost_per_million_tokens
                    elif instance_price_per_hour is not None and throughput == 0:
                        point_data["cost_per_million_tokens"] = float('inf')

                    parsed_data_points.append(point_data)
                    print(f"Successfully parsed common metrics from {filename}")
                    if latency is None:
                        print(f"  - Note: 'avg_per_token_latency_ms' not found in {filename}.")
                    if normalized_latency is None:
                        print(f"  - Note: 'avg_normalized_time_per_output_token_ms' not found in {filename}.")
                    if p90_normalized_latency is None:
                        print(f"  - Note: 'p90_normalized_time_per_output_token_ms' not found in {filename}.")
                else:
                    missing_core = []
                    if throughput is None: missing_core.append("'throughput'")
                    if request_rate is None: missing_core.append("'request_rate'")
                    print(f"Warning: Missing core metric(s) ({', '.join(missing_core)}) in {filename}. Skipping this file for plots.")

            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Error reading or parsing {file_path}: {e}")
            except Exception as e:
                print(f"An unexpected error occurred with {file_path}: {e}")

    return parsed_data_points

def _prepare_plot_data(all_folders_data, x_key, y_key):
    """Prepares data for plotting from the parsed folder data."""
    plot_data = {}
    for folder_name, points in all_folders_data.items():
        valid_points = [p for p in points if p.get(x_key) is not None and p.get(y_key) is not None]
        if valid_points:
            plot_data[folder_name] = {
                'x': [p[x_key] for p in valid_points],
                'y': [p[y_key] for p in valid_points]
            }
    return plot_data

def analyze_and_plot(folder_paths, instance_price_per_hour=None):
    """
    Scans one or more folders for JSON files, extracts benchmark metrics,
    and plots comparison charts with each folder as a separate line.

    Args:
        folder_paths (list[str]): A list of paths to folders containing JSON files.
        instance_price_per_hour (float, optional): The instance price per hour for cost calculation.
    """
    all_folders_data = {}

    for folder_path in folder_paths:
        parsed_data = _parse_folder_data(folder_path, instance_price_per_hour)
        if parsed_data:
            folder_name = os.path.basename(os.path.normpath(folder_path))
            all_folders_data[folder_name] = parsed_data

    if not all_folders_data:
        print("No data points were parsed from any folder. Cannot generate plots.")
        return

    # Print summaries for each folder
    for folder_name, parsed_data in all_folders_data.items():
        print(f"\n--- Summary for: {folder_name} ---")
        _print_summary(parsed_data)

    # --- Plot 1: Throughput vs. Per Token Latency ---
    plot1_data = _prepare_plot_data(all_folders_data, x_key='latency', y_key='throughput')
    if plot1_data:
        _create_line_plot(
            all_series_data=plot1_data,
            x_label='Average Per Token Latency (ms)',
            y_label='Throughput (output tokens/sec)',
            title='Throughput vs. Per Token Latency',
            output_filename_base='throughput_vs_latency_comparison'
        )
    else:
        print("No data available for 'Throughput vs. Per Token Latency' plot.")

    # --- Plot 2: Throughput vs. Normalized Per Token Latency ---
    plot2_data = _prepare_plot_data(all_folders_data, x_key='normalized_latency', y_key='throughput')
    if plot2_data:
        _create_line_plot(
            all_series_data=plot2_data,
            x_label='Average Normalized Time Per Output Token (ms)',
            y_label='Throughput (output tokens/sec)',
            title='Throughput vs. Normalized Per Token Latency',
            output_filename_base='throughput_vs_normalized_latency_comparison'
        )
    else:
        print("No data available for 'Throughput vs. Normalized Per Token Latency' plot.")

    # --- Plot 3: Throughput vs. P90 Normalized Per Token Latency ---
    plot3_data = _prepare_plot_data(all_folders_data, x_key='p90_normalized_latency', y_key='throughput')
    if plot3_data:
        _create_line_plot(
            all_series_data=plot3_data,
            x_label='P90 Normalized Time Per Output Token (ms)',
            y_label='Throughput (output tokens/sec)',
            title='Throughput vs. P90 Normalized Per Token Latency',
            output_filename_base='throughput_vs_p90_normalized_latency_comparison'
        )
    else:
        print("No data available for 'Throughput vs. P90 Normalized Per Token Latency' plot.")

    # --- Plot 4: Cost per Million Output Tokens vs. Normalized Per Token Latency ---
    if instance_price_per_hour is not None:
        plot4_data = _prepare_plot_data(all_folders_data, x_key='normalized_latency', y_key='cost_per_million_tokens')
        if plot4_data:
            _create_line_plot(
                all_series_data=plot4_data,
                x_label='Average Normalized Time Per Output Token (ms)',
                y_label='$ per Million Output Tokens',
                title='Cost per Million Output Tokens vs. Normalized Latency',
                output_filename_base='cost_vs_normalized_latency_comparison'
            )
        else:
            print("No data available for 'Cost vs. Normalized Latency' plot.")
    else:
        print("Skipping cost plot as --instance-price-per-hour was not provided.")

    # --- Plot 5: Throughput vs. Request Rate (QPS) ---
    plot5_data = _prepare_plot_data(all_folders_data, x_key='request_rate', y_key='throughput')
    if plot5_data:
        _create_line_plot(
            all_series_data=plot5_data,
            x_label='Request Rate (QPS)',
            y_label='Throughput (output tokens/sec)',
            title='Throughput vs. Request Rate (QPS)',
            output_filename_base='throughput_vs_qps_comparison'
        )
    else:
        print("No data available for 'Throughput vs. Request Rate (QPS)' plot.")

    # --- Plot 6: P90 Normalized Latency vs. Request Rate (QPS) ---
    plot6_data = _prepare_plot_data(all_folders_data, x_key='request_rate', y_key='p90_normalized_latency')
    if plot6_data:
        _create_line_plot(
            all_series_data=plot6_data,
            x_label='Request Rate (QPS)',
            y_label='P90 Normalized Time Per Output Token (ms)',
            title='P90 Normalized Latency vs. Request Rate (QPS)',
            output_filename_base='p90_latency_vs_qps_comparison'
        )
    else:
        print("No data available for 'P90 Normalized Latency vs. Request Rate (QPS)' plot.")

if __name__ == '__main__':
    # Set up an argument parser to get the folder path from the command line
    parser = argparse.ArgumentParser(description="Parse all benchmark JSON files in one or more folders and generate comparison plots.")
    parser.add_argument("folder_paths", type=str, nargs='+', help="One or more paths to folders containing the JSON files.")
    parser.add_argument("--instance-price-per-hour", type=float, default=None,
                        help="Optional: Instance price per hour (e.g., 2.50 for $2.50/hour) to calculate cost per million tokens.")

    args = parser.parse_args()

    # Call the function with the folder paths provided by the user
    analyze_and_plot(args.folder_paths, args.instance_price_per_hour)
