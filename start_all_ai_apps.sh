#!/bin/bash

AI_BASE_DIR=`pwd`
CURRENT_DIR=`pwd`

eval "$(conda shell.bash hook)"
conda activate genai

cd $AI_BASE_DIR/ai_notes_converter
./run_in_background.sh 

cd $AI_BASE_DIR/app_monitor
./run_in_background.sh

cd $AI_BASE_DIR/viz_builder
./run_in_background.sh

cd $AI_BASE_DIR/course_creator
./run_in_background.sh

cd $AI_BASE_DIR/ai_dataset_generator
./run_in_background.sh

cd $AI_BASE_DIR/ai_accessibility
./run_in_background.sh

cd $AI_BASE_DIR/ai_notes_converter
./run_in_background.sh

cd $CURRENT_DIR
