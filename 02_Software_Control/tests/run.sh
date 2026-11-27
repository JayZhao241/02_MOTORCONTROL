#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
test_binary=$(mktemp /tmp/motorcontrol-tests.XXXXXX)
trap 'rm -f "$test_binary"' EXIT
"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
    -Itests/stubs -ICore/BSP tests/test_communication.c \
    Core/BSP/emm_v5.c Core/BSP/zdt_x42s.c Core/BSP/motor_app.c -o "$test_binary"
"$test_binary"
"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined,float-cast-overflow \
    -Itests/stubs -ICore/BSP tests/test_motor.c Core/BSP/motor.c -o "$test_binary"
"$test_binary"
"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
    -Itests/stubs -ICore/BSP tests/test_keys.c -o "$test_binary"
"$test_binary"

"${CC:-cc}" -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
    -Itests/stubs -ICore/BSP tests/test_bridge.c Core/BSP/motor_bridge.c \
    Core/BSP/emm_v5.c -o "$test_binary"
"$test_binary"
