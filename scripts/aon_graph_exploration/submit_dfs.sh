#!/bin/bash
#SBATCH --job-name=guinness_dfs
#SBATCH --output=logs/dfs_search_%j.log
#SBATCH --error=logs/dfs_search_%j.err
#SBATCH --nodes=1               # Must be 1 for ProcessPoolExecutor
#SBATCH --ntasks=1              # Single task spanning multiple cores
#SBATCH --cpus-per-task=68      # Request all 68 cores on the node
#SBATCH --time=48:00:00         # Give it up to 48 hours to run
#SBATCH --mem=64G               # Graph is small, but 60 workers need some overhead

# Ensure the logs directory exists (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${SCRIPT_DIR}/logs"

# If you use a virtual environment, activate it here
# source /path/to/your/venv/bin/activate

echo "Starting Guinness DFS Search on 68 cores..."
python -u "${SCRIPT_DIR}/baseline_dfs_search.py"
echo "Search finished."