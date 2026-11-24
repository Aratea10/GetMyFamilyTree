"""Measure research recommendation performance with synthetic trees"""

import argparse
import gc
import tempfile
import tracemalloc
from itertools import pairwise
from pathlib import Path
from time import perf_counter

from getmyfamilytree.cli import Checkpoint
from getmyfamilytree.reports import FAMILIES_URL, PERSON_URL
from getmyfamilytree.research_recommendations import build_recommendations


def make_identifier(index):
    return f"{index // 1000:04d}-{index % 1000:03d}"


def parent_child(child, *parents):
    return {
        "child": {"resourceId": child},
        **{
            f"parent{index}": {"resourceId": parent}
            for index, parent in enumerate(parents, 1)
        },
    }


def couple(first, second):
    return {
        "type": "http://gedcomx.org/Couple",
        "person1": {"resourceId": first},
        "person2": {"resourceId": second},
    }


def populate(checkpoint, size, shape):
    identifiers = [make_identifier(index) for index in range(size)]

    checkpoint.set_origin(identifiers[0])
    checkpoint.seed(identifiers)

    for fid in identifiers:
        checkpoint.save_response(
            PERSON_URL + fid,
            {
                "persons": [
                    {
                        "id": fid,
                        "display": {"name": f"Person {fid}"},
                    }
                ]
            },
        )

        checkpoint.finish(fid, [])

    if shape == "chain":
        for parent, child in pairwise(identifiers):
            checkpoint.save_response(
                FAMILIES_URL.format(parent),
                {"childAndParentsRelationships": [parent_child(child, parent)]},
            )

    elif shape == "overlap":
        blood_count = (size + 1) // 2
        blood = identifiers[:blood_count]
        spouses = identifiers[blood_count:]

        for index, person in enumerate(blood):
            relationships = []
            parentage = []

            spouse = spouses[index] if index < len(spouses) else None

            if spouse:
                relationships.append(couple(person, spouse))

            if index + 1 < len(blood):
                child = blood[index + 1]
                parents = [person]

                if spouse:
                    parents.append(spouse)

                parentage.append(parent_child(child, *parents))

            checkpoint.save_response(
                FAMILIES_URL.format(person),
                {
                    "relationships": relationships,
                    "childAndParentsRelationships": parentage,
                },
            )

    return identifiers


def benchmark(size, shape, measure_memory):
    with tempfile.TemporaryDirectory() as directory:
        checkpoint = Checkpoint(Path(directory) / "benchmark.sqlite")

        try:
            identifiers = populate(checkpoint, size, shape)

            gc.collect()

            if measure_memory:
                tracemalloc.start()

            try:
                started = perf_counter()

                result = build_recommendations(checkpoint, identifiers)

                elapsed = perf_counter() - started

                peak = tracemalloc.get_traced_memory()[1] if measure_memory else None

            finally:
                if measure_memory:
                    tracemalloc.stop()

            if len(result) != size:
                raise RuntimeError("Unexpected recommendation count")

            print(f"People: {size:,}")
            print(f"Tree shape: {shape}")
            print(f"Recommendations: {len(result):,}")
            print(f"Elapsed time: {elapsed:.4f} seconds")

            if peak is not None:
                print(f"Peak Python allocations: {peak / (1024 * 1024):.2f} MiB")
                print("Memory tracing enabled")

        finally:
            checkpoint.close()


def main():
    parser = argparse.ArgumentParser(description="Benchmark genealogy recommendations.")

    parser.add_argument(
        "--size",
        type=int,
        default=1000,
    )

    parser.add_argument(
        "--shape",
        choices=("chain", "overlap"),
        default="chain",
    )

    parser.add_argument(
        "--memory",
        action="store_true",
    )

    args = parser.parse_args()

    if not 1 <= args.size <= 10_000_000:
        parser.error("--size must be between 1 and 10000000")

    benchmark(
        args.size,
        args.shape,
        args.memory,
    )


if __name__ == "__main__":
    main()
