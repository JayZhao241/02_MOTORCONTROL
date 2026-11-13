#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
test_binary=$(mktemp /tmp/motorcontrol-tests.XXXXXX)
trap 'rm -f "$test_binary"' EXIT
"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
    -Itests/stubs -ICore/BSP tests/test_communication.c -o "$test_binary"
"$test_binary"
