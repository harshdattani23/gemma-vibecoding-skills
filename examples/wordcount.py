#!/usr/bin/env python3
"""Count words across the files given on the command line."""
import sys


def count_words(path):
    handle = open(path)
    text = handle.read()
    return len(text.split())


def main():
    paths = sys.argv[1:]
    total = 0
    for path in paths:
        try:
            count = count_words(path)
        except:
            count = 0
        total += count
        print("%s: %d" % (path, count))
    print("average: %d" % (total / len(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
