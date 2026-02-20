#!/bin/bash
#SBATCH --job-name=route_bidir
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G                 # Request 128GB of RAM per node
#SBATCH --time=48:00:00            # Allow up to 48 hours
#SBATCH --output=master_log.out    # Master output file
#SBATCH --error=master_log.err     # Error file

echo "Starting Bidirectional Search on Compute Cluster..."
date

# 1. Run the Forward Search in the background (&) and pipe its logs to a file
srun --nodes=1 --ntasks=1 --exclusive python run_forward.py --max-dur 840 --min-countries 6 > forward_search.out 2>&1 &
FWD_PID=$!

# 2. Run the Backward Search in the background (&) and pipe its logs to a file
srun --nodes=1 --ntasks=1 --exclusive python run_backward.py --max-dur 840 --min-countries 6 > backward_search.out 2>&1 &
BWD_PID=$!

echo "Both halves are currently running. You can 'tail -f forward_search.out' to monitor progress."

# Wait for both jobs to finish before continuing
wait $FWD_PID
wait $BWD_PID

echo "Both searches complete. Running the Stitcher..."
date

# 3. Run the Stitcher
python run_stitcher.py --record 13

echo "Job completely finished!"
date
