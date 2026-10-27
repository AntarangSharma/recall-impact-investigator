from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from recall.agent import Investigator
from recall.inventory import read_inventory
from recall.llm import load_local_env
from recall.sources import DATA
from recall.store import Store


def paths() -> tuple[Store, Path]:
    runtime = Path(os.getenv("RECALL_RUNTIME", "runtime"))
    return Store(runtime / "app.db"), runtime / "checkpoints.db"


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["demo", "eval-fixtures", "eval-live", "smoke-live", "refresh-data"]
    )
    args = parser.parse_args()
    if args.command == "demo":
        store, checkpoints = paths()
        agent = Investigator(store, checkpoints)
        data = (DATA / "demo.csv").read_bytes()
        identity, _ = store.create(data, read_inventory(data), "fixture")
        try:
            agent.run(identity)
            print(
                json.dumps(
                    {
                        "investigation_id": identity,
                        "status": store.investigation(identity)["status"],
                        "url": f"http://127.0.0.1:8000/investigations/{identity}",
                    },
                    indent=2,
                )
            )
        finally:
            agent.close()
    else:
        from recall.evaluation import execute

        execute(args.command)


if __name__ == "__main__":
    main()
