#!/bin/bash

set -e

cd "$(dirname "$0")/.."

(
    git ls-files '*.sh'
    git grep -l '#![^ ]*[s]h'
) | \
    sort -u | \
    grep -v '^manifests/pxeboot/kickstart.ks$' | \
    xargs shellcheck
