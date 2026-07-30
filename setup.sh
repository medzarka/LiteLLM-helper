#!/bin/bash
# Setup script to initialize host storage directories for the 'ai-ui' stack.
# Run this script on the target ARM host machine before running docker-compose up -d

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "Loading configuration from .env file..."
  # Export variables from .env, ignoring comments
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

# Base directory where all volume data will be stored on the host
if [ -z "${WORKDIR}" ]; then
  echo "Error: WORKDIR environment variable is not set."
  echo "Please set it before running this script (e.g., export WORKDIR=/path/to/data)"
  exit 1
fi

echo "Setting up volume directories for ai-ui (LiteLLM + Open-WebUI) in ${WORKDIR}..."

# Directories for Open-WebUI, LiteLLM, Qdrant data
sudo mkdir -p ${WORKDIR}/open-webui
sudo mkdir -p ${WORKDIR}/litellm
sudo mkdir -p ${WORKDIR}/qdrant
# Added for LiteLLM Helper internal database
sudo mkdir -p ${WORKDIR}/litellm-helper

echo "Setting permissions..."
sudo chown -R $USER:$USER ${WORKDIR}
sudo chmod -R 775 ${WORKDIR}

echo "Directories created successfully!"
