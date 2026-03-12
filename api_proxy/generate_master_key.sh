#!/usr/bin/env bash
set -euo pipefail

# Generate and print a secure 256-bit master key
openssl rand -hex 32
