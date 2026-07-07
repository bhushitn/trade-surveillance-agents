"""CLI: python -m datagen --preset full --out eval/dataset"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from datagen.config import GeneratorConfig
from datagen.generator import generate, write_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic surveillance dataset")
    parser.add_argument("--preset", choices=["full", "ci"], default="full")
    parser.add_argument("--seed", type=int, default=None, help="Override the preset seed")
    parser.add_argument("--out", type=Path, default=Path("eval/dataset"))
    args = parser.parse_args()

    config = GeneratorConfig.full() if args.preset == "full" else GeneratorConfig.ci()
    if args.seed is not None:
        config = replace(config, seed=args.seed)

    events, episodes = generate(config)
    manifest = write_dataset(events, episodes, args.out, config)
    print(f"wrote {manifest['n_events']} events, {manifest['n_episodes']} episodes to {args.out}")
    print(f"events sha256: {manifest['events_sha256']}")


if __name__ == "__main__":
    main()
