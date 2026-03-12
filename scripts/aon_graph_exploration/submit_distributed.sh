#!/bin/bash
#SBATCH --job-name=elite_array
#SBATCH --output=logs/dist_%A_%a.out
#SBATCH --error=logs/dist_%A_%a.err
#SBATCH --array=0-99
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --time=48:00:00

# Load your python environment if necessary (uncomment and modify if needed)
# source python_env/bin/activate

echo "====================================================="
echo "Starting Distributed DFS Worker"
echo "Master Job ID: $SLURM_ARRAY_JOB_ID"
echo "Array Task ID: $SLURM_ARRAY_TASK_ID"
echo "====================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export SLURM_CPUS_PER_TASK=2

# Unleash the worker on its specific slice of the graph
python "${SCRIPT_DIR}/distributed_dfs_search.py" --job_idx "$SLURM_ARRAY_TASK_ID" --total_jobs 100

echo "Worker $SLURM_ARRAY_TASK_ID completed successfully."