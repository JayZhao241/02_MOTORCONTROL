"""Compare production encoders with inspected functions from the supplied RAR.
Read-only source extraction. No vendor binary, startup, UART or motor is run.
Usage: python3 tests/check_vendor_reference.py [--vendor-root /path/to/9.例程]
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--vendor-root', type=Path, default=Path(
    '/Users/zhaoyingjie/Documents/mac/STM32/Project/00_Document/'
    'ZDT_XS系列第二代闭环步进电机资料/9. 例程_STM32F407'))
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
archive = args.vendor_root / 'HAL库/Emm固件模式/串口通讯/STM32F407_串口通讯__位置模式.rar'
raw = subprocess.check_output(['tar', '-xOf', str(archive),
    'STM32F407_串口通讯__位置模式/Src/Emm_V5.c'])
try:
    source = raw.decode('utf-8')
except UnicodeDecodeError:
    source = raw.decode('gb18030')
functions = []
for name in ('Emm_V5_Pos_Control', 'Emm_V5_En_Control', 'Emm_V5_Stop_Now'):
    start = source.index('void ' + name + '(')
    i = source.index('{', start) + 1
    depth = 1
    while depth:
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
        i += 1
    functions.append(source[start:i])
harness = '''#include <assert.h>
#include <string.h>
#include <stdio.h>
#include "emm_v5.h"
#define __IO volatile
static int huart1;
static uint8_t expected[16];
static unsigned expected_len;
static void HAL_UART_Transmit_DMA(int *h, uint8_t *b, unsigned n)
{ (void)h; memcpy(expected,b,n); expected_len=n; }
''' + '\n'.join(functions) + '''
int main(void) {
 uint8_t out[13]; unsigned seed=241U;
 for(unsigned i=0;i<256;i++) {
  seed=seed*1664525U+1013904223U;
  EMM_V5_Position_t m={(uint8_t)(1U+i%255U),i%2U,1U+i%3000U,i%256U,seed?seed:1U,i%3U,10000U};
  Emm_V5_Pos_Control(m.address,m.direction,m.speed_rpm,m.acceleration,m.pulses,m.mode,false);
  assert(EMM_V5_BuildPosition(&m,out,13) && expected_len==13 && !memcmp(out,expected,13));
  Emm_V5_En_Control(m.address,i%2U,false);
  assert(EMM_V5_BuildEnable(m.address,i%2U,out,13) && expected_len==6 && !memcmp(out,expected,6));
  Emm_V5_Stop_Now(m.address,false);
  assert(EMM_V5_BuildStop(m.address,out,13) && expected_len==5 && !memcmp(out,expected,5));
 }
 puts("768 packet comparisons against original vendor functions passed");
}
'''
with tempfile.TemporaryDirectory(prefix='motor-vendor-') as directory:
    test_source = Path(directory) / 'parity.c'
    executable = Path(directory) / 'parity'
    test_source.write_text(harness)
    subprocess.run([*shlex.split(os.environ.get('CC', 'cc')), '-std=c11', '-Wall',
        '-Wextra', '-Werror', '-fsanitize=address,undefined', '-I' + str(repo / 'Core/BSP'),
        str(test_source), str(repo / 'Core/BSP/emm_v5.c'), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
