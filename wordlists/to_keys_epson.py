#!/usr/bin/python3
# SPDX-License-Identifier: AGPL-3.0-or-later
import sys

def caesar(b, shift=1):
    return bytes(c + shift for c in b)

def line_to_key(line):
    line = line[:-1]
    return caesar(f'{line[0].upper()}{line[1:8].lower():.<7}'.encode('ascii')).decode('ascii') if line else ''

if __name__ == '__main__':
    for line in sys.stdin:
        sys.stdout.write(line_to_key(line)+'\n')
