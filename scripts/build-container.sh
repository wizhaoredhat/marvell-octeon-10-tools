#!/bin/bash

TAG="${TAG:-quay.io/$USER/marvell-tools:latest}"

set -ex

TAG="${1:-$TAG}"

buildah manifest rm marvell-tools-manifest || true
buildah manifest create marvell-tools-manifest
buildah build --manifest marvell-tools-manifest --platform linux/amd64,linux/arm64 -t "$TAG" .
buildah manifest push --all marvell-tools-manifest "docker://$TAG"
