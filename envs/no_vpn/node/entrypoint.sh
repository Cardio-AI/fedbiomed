#!/bin/bash

# source venv/bin/activate
source /miniconda/bin/activate
conda activate fedbiomed-node
python -m fedbiomed.node.cli --start --gpu 