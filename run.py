# run_pipeline.py

import subprocess
import sys
import os


def run(cmd):
    print(f"\n▶ Running: {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"❌ Failed: {cmd}")
        sys.exit(1)


def main():

    # Activate virtual environment automatically (Windows)
    venv_python = ".venv\\Scripts\\python.exe"

    if not os.path.exists(venv_python):
        print("❌ Virtual environment not found")
        sys.exit(1)

    python = venv_python

    run(f"{python} webcrawl.py")

    run(
        f"{python} testgen.py "
        f"--graph results/transition_graph.json "
        f"--force-kb "
        f"--model llama-3.1-8b-instant"
    )

    run(
        f"{python} execute_tests.py "
        f"--input results/test_cases.json"
    )

    print("\n✅ FULL PIPELINE COMPLETE")


if __name__ == "__main__":
    main()