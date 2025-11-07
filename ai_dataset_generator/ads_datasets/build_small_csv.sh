#!/bin/bash

# This script finds all .csv files in the current directory,
# takes the first 5001 lines, and saves them to a new
# file named [original_name]_small.csv.

echo "Starting CSV processing..."

cd ./csv

# Loop through all files ending in .csv in the current directory
for f in *.csv
do
  # This check handles the case where no .csv files are found
  # and the loop tries to run on the literal string "*.csv"
  if [ -f "$f" ]; then
    
    # Create the new filename
    # ${f%.csv} is a Bash feature that removes ".csv" from the end of the string
    new_file="${f%.csv}_small.csv"

    # Run the head command and redirect the output to the new file
    head -n 5001 "$f" > "$new_file"

    # Print a confirmation message
    echo "Created $new_file from $f"
  
  else
    # If no files are found, print a message and stop
    echo "No .csv files found to process."
    break
  fi
done

echo "Processing complete."

cd -