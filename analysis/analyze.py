import json
import matplotlib.pyplot as plt
import os
import argparse

def parse_and_plot(folder_path):
    """
    Scans a folder for JSON files, extracts benchmark metrics, and plots
    throughput vs. per-token latency.

    Args:
        folder_path (str): The path to the folder containing the JSON files.
    """
    throughputs = []
    latencies = []
    request_rates = []

    print(f"Scanning folder: {folder_path}")

    # Check if the folder path exists and is a directory
    if not os.path.isdir(folder_path):
        print(f"Error: The provided path '{folder_path}' is not a valid directory.")
        return

    # Loop through each file in the specified directory
    for filename in os.listdir(folder_path):
        # Process only files that end with .json
        if filename.lower().endswith('.json'):
            file_path = os.path.join(folder_path, filename)
            try:
                # Open and load the JSON data from the file
                with open(file_path, 'r') as f:
                    data = json.load(f)

                # Extract the required metrics from the JSON data
                metrics = data.get("metrics", {})
                throughput = metrics.get("throughput")
                latency = metrics.get("avg_per_token_latency_ms")
                request_rate = metrics.get("request_rate")

                # Append the extracted data to our lists if all are found
                if all([throughput, latency, request_rate]):
                    throughputs.append(throughput)
                    latencies.append(latency)
                    request_rates.append(request_rate)
                    print(f"Successfully parsed {filename}")
                else:
                    print(f"Warning: Missing one or more metrics in {filename}")

            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Error reading or parsing {file_path}: {e}")
            except Exception as e:
                print(f"An unexpected error occurred with {file_path}: {e}")

    if not throughputs:
        print("No valid data was parsed from the folder. Cannot generate a plot.")
        return

    # --- Plotting ---
    plt.figure(figsize=(10, 6))
    scatter = plt.scatter(latencies, throughputs, c=request_rates, cmap='viridis', s=100, alpha=0.8)

    # Add labels and title to the plot
    plt.title('Throughput vs. Per Token Latency')
    plt.xlabel('Average Per Token Latency (ms)')
    plt.ylabel('Throughput (output tokens/sec)')

    # Add a color bar for reference
    cbar = plt.colorbar(scatter)
    cbar.set_label('Request Rate (QPS)')

    # Annotate each point with its request rate
    for i, rate in enumerate(request_rates):
        plt.annotate(f'{rate} qps', (latencies[i], throughputs[i]), textcoords="offset points", xytext=(0,10), ha='center')

    plt.grid(True)

    # Save the plot to a file
    output_filename = 'throughput_vs_latency.png'
    plt.savefig(output_filename)
    print(f"Chart saved to {output_filename}")

    # Display the plot
    plt.show()


if __name__ == '__main__':
    # Set up an argument parser to get the folder path from the command line
    parser = argparse.ArgumentParser(description="Parse all benchmark JSON files in a folder and generate a plot.")
    parser.add_argument("folder_path", type=str, help="The path to the folder containing the JSON files.")

    args = parser.parse_args()

    # Call the function with the folder path provided by the user
    parse_and_plot(args.folder_path)
