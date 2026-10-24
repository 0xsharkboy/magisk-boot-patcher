import sys

USAGE = "usage: magisk-boot-patcher <command> [options]\n"


def main(argv=None) -> int:
    sys.stdout.write(USAGE)
    return 0
