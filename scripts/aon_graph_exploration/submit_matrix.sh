#!/bin/bash
#SBATCH --job-name=matrix_dp
#SBATCH --output=logs/matrix_%j.out
#SBATCH --error=logs/matrix_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=64G
#SBATCH --time=48:00:00

# Load your python environment if necessary
# source python_env/bin/activate

echo "====================================================="
echo "Starting Sparse Matrix DP Solver"
echo "Job ID: $SLURM_JOB_ID"
echo "====================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the matrix solver
python "${SCRIPT_DIR}/matrix_dp_solver.py"

echo "Matrix Solver completed successfully."