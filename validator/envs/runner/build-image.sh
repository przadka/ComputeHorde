#!/bin/bash -eux

IMAGE_NAME="backenddevelopersltd/compute-horde-validator-runner:v0-latest"
rsync -avzP ../../nginx/config_helpers data/nginx/
docker build -t $IMAGE_NAME .
