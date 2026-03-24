import subprocess
import sys

def run_command(command):
    print(f"\nRunning: {command}\n")
    result = subprocess.run(command, shell=True)

    if result.returncode != 0:
        print(f"Error while running: {command}")
        sys.exit(1)


def main():

    print("===================================")
    print(" Starting Full Automation Pipeline ")
    print("===================================")

    # Step 1: Crawl website
    run_command("python webcrawl.py")

    # Step 2: Generate test cases
    run_command(
        "python testgen.py --graph results/transition_graph.json "
        "--force-kb --model llama-3.1-8b-instant"
    )

    # Step 3: Execute test cases
    run_command(
        "python execute_tests.py --input results/test_cases.json"
    )

    print("\n===================================")
    print(" Pipeline Completed Successfully ✅")
    print("===================================")


if __name__ == "__main__":
    main()