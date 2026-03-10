#!/bin/bash
#SBATCH --job-name=doom_dp
#SBATCH --output=logs/doom_%j.out
#SBATCH --error=logs/doom_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G       # Set a limit like 16GB or 32GB to trigger the crash faster
#SBATCH --time=00:30:00 # 30 minutes is more than enough for the explosion

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python "${SCRIPT_DIR}/doomed_backward_dp.py"