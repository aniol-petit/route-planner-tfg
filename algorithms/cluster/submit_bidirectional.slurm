#!/bin/bash
#SBATCH --job-name=route_bidir
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=68
#SBATCH --mem=128G                 # Request 128GB of RAM per node
#SBATCH --time=48:00:00            # Allow up to 48 hours
#SBATCH --output=master_log.out    # Master output file
#SBATCH --error=master_log.err     # Error file

module load conda
source $(conda info --base)/etc/profile.d/conda.sh
conda activate python_env

echo "Starting Bidirectional Search on Compute Cluster..."
date

cd /data/upftfg32/apetit/route_planner_tfg/algorithms/cluster

# CRITICAL: Clean up old artifacts before starting a new run!
rm -f temp_*.pkl live_labels_*.pkl *.flag

# 1. Run the Forward Search in the background (&) 
srun --nodes=1 --ntasks=1 --exclusive python -u run_forward.py --max-dur 840 --min-countries 6 > forward_search.out 2>&1 &
FWD_PID=$!

# 2. Run the Backward Search in the background (&) 
srun --nodes=1 --ntasks=1 --exclusive python -u run_backward.py --max-dur 840 --min-countries 6 > backward_search.out 2>&1 &
BWD_PID=$!

# 3. Run the Stitcher continuously in the background (&)
python -u run_stitcher.py --record 13 > stitcher_search.out 2>&1 &
STITCHER_PID=$!

echo "All three processes are running concurrently."
echo "- Tail 'forward_search.out' and 'backward_search.out' for progress."
echo "- Tail 'stitcher_search.out' for LIVE route discoveries!"

# Wait for both search jobs to hit their "done" flags
wait $FWD_PID
wait $BWD_PID

# The stitcher will naturally exit after it reads the flags.
wait $STITCHER_PID

echo "Job completely finished!"
date