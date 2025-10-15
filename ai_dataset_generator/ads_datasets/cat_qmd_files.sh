#!/bin/bash

# Find all .qmd files recursively and process them
find . -name "*.qmd" -type f | while read -r file; do
    # Output the full file path
    echo "$file"
    
    # Output two blank lines
    echo
    echo
    
    # Output the contents of the file
    cat "$file"
    
    # Add a separator between files (optional - you can remove this if not needed)
    echo
    echo "--- End of $file ---"
    echo
done