#!/bin/bash

AI_BASE_DIR=`pwd`
CURRENT_DIR=`pwd`

cd $AI_BASE_DIR/app_monitor
./run_in_background.sh

cd $AI_BASE_DIR/viz_builder
./run_in_background.sh

cd $AI_BASE_DIR/course_creator
./run_in_background.sh

cd $CURRENT_DIR
