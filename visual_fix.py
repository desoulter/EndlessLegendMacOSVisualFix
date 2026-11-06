#!/usr/bin/env python3
"""Restore movement path colors and turn numbers in Endless Legend on macOS."""

import argparse
from pathlib import Path
import sys
import zlib

from elvisualfix import __version__
from elvisualfix.assets import AssetError
from elvisualfix.installer import DEFAULT_GAME, RESOURCE, inspect, mutate
from elvisualfix.shaders import LAVA_SHADER


def locations(game, resource):
    if resource is not None:
        resource = resource.expanduser().resolve()
        if len(resource.parents) >= 5 and resource.parents[3].name.endswith(".app"):
            state = resource.parents[4] / "VisualFix"
        else:
            state = resource.parent / "VisualFix"
        return resource, state
    game = (DEFAULT_GAME if game is None else game).expanduser().resolve()
    if game.name.endswith(".app"):
        return game / "Contents/Resources/Data/resources.assets", game.parent / "VisualFix"
    return game / RESOURCE, game / "VisualFix"


def show_reports(reports):
    for report in reports:
        print("  " + report.shader.name + ": " + report.state +
              " (" + str(report.affected_programs) + " affected programs)")
    if not any(report.shader.name == LAVA_SHADER for report in reports):
        print("  " + LAVA_SHADER + ": not present")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", choices=("status", "verify", "install", "uninstall"))
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--game", type=Path, help="Game folder or EndlessLegend.app")
    location.add_argument("--resource", type=Path, help="Explicit resources.assets path")
    parser.add_argument("--dry-run", action="store_true", help="Analyze installation without writing files")
    args = parser.parse_args(argv)
    if args.dry_run and args.command != "install":
        parser.error("--dry-run is available with install")
    resource, state = locations(args.game, args.resource)
    try:
        print("Endless Legend macOS VisualFix " + __version__)
        if args.command in ("status", "verify"):
            version, reports = inspect(resource)
            print("Unity " + version)
            show_reports(reports)
            if args.command == "verify":
                if any(report.changes for report in reports):
                    print("The shader fix is needed. Run install.")
                    return 2
                print("Repaired shader code verified.")
        else:
            result = mutate(resource, state, args.command, dry_run=args.dry_run)
            if args.dry_run:
                show_reports(result["reports"])
                print("Would repair " + str(result["planned"]) + " shader objects. No files changed.")
            elif not result["changed"]:
                show_reports(result["reports"])
                print("Nothing to change.")
            else:
                print("Installed successfully." if args.command == "install" else "Removed successfully.")
                print("Restart Endless Legend.")
                print("Backup: " + str(result["backup"]))
        return 0
    except (AssetError, OSError, ValueError, KeyError, UnicodeError, zlib.error) as error:
        print("VisualFix: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
