#!/bin/bash

set -e

_parse() {
    sed -e 's/\r$//' "$1" | \
      sed -n 's/.* serial\[\/dev\/ttyUSB0\]: read [^"'\'']\+\(.*\)$/\1/p'
}

_parse_to_py() {
    printf 'a = ( '\\\\'\n'
    _parse "$1" | sed 's/$/ + \\/'
    printf '"" )\n'
}

_parse_to_py "$1" > /tmp/marvell_tools_pxeboot_output.py
cd /tmp
python -c 'if True:
    import marvell_tools_pxeboot_output as o
    print(o.a)
'
