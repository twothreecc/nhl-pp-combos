"""Run the full pipeline: fetch -> parse -> PP TOI -> aggregate -> site/data.json.

Run from the repo root:  python pipeline/run.py
"""
import time

import aggregate
import build_data
import fetch
import parse
import pp_toi


def main():
    for name, step in (("fetch", fetch), ("parse", parse), ("pp_toi", pp_toi),
                       ("aggregate", aggregate), ("build_data", build_data)):
        t = time.time()
        print(f"\n== {name} ==")
        step.main()
        print(f"({time.time() - t:.0f}s)")


if __name__ == "__main__":
    main()
