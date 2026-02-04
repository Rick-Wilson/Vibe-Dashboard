#!/usr/bin/env python3
"""
LOC History Accumulator
=======================
Measures actual lines of code at historical points by checking out past commits
and running tokei. Stores results in loc_history.json for use by the dashboard.

Uses a separate temporary clone for historical measurements to avoid disrupting
any uncommitted work in your actual repositories.

Usage:
    # Backfill last 40 days
    python accumulate_loc_history.py --path /path/to/repos --days 40

    # Update with today's data only
    python accumulate_loc_history.py --path /path/to/repos

    # Backfill specific date range
    python accumulate_loc_history.py --path /path/to/repos --start 2025-12-01 --end 2026-01-31
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOC_HISTORY_FILE = "loc_history.json"
TEMP_CLONE_DIR = None  # Will be set to a temp directory


def run_git(repo_path: Path, args: list) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + args,
        capture_output=True, text=True
    )
    return result.stdout.strip()


def run_tokei(repo_path: Path) -> dict:
    """Run tokei and return {language: lines} dict."""
    try:
        result = subprocess.run(
            ["tokei", "--output", "json", str(repo_path)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return {
                lang: info.get("code", 0)
                for lang, info in data.items()
                if lang not in ("Total", "HTML", "SVG") and isinstance(info, dict) and info.get("code", 0) > 0
            }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return {}


def get_commit_at_date(repo_path: Path, date: datetime) -> str:
    """Get the commit hash that was HEAD at a given date."""
    date_str = date.strftime("%Y-%m-%d 23:59:59")
    commit = run_git(repo_path, ["rev-list", "-1", f"--before={date_str}", "HEAD"])
    return commit


def get_repo_created_date(repo_path: Path) -> datetime:
    """Get the date of the first commit in the repo."""
    # Get all commit dates and take the last one (oldest)
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--format=%aI"],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        dates = result.stdout.strip().split('\n')
        first_commit_date = dates[-1]  # Last line is oldest commit
        try:
            return datetime.fromisoformat(first_commit_date.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now()


def get_or_create_temp_clone(repo_path: Path, temp_base: Path) -> Path:
    """Get or create a temporary clone of the repo for safe historical checkouts."""
    repo_name = repo_path.name
    temp_clone = temp_base / repo_name

    if not temp_clone.exists():
        # Clone with --shared to save disk space (uses original repo's objects)
        subprocess.run(
            ["git", "clone", "--shared", "--quiet", str(repo_path), str(temp_clone)],
            capture_output=True, check=True
        )
    else:
        # Fetch latest from original repo
        subprocess.run(
            ["git", "-C", str(temp_clone), "fetch", "--quiet", "origin"],
            capture_output=True
        )

    return temp_clone


def measure_loc_at_commit(repo_path: Path, commit: str, temp_base: Path) -> dict:
    """Checkout a commit in a temp clone and measure LOC. Original repo is untouched."""
    if not commit:
        return {}

    # Use a temporary clone for checkout
    temp_clone = get_or_create_temp_clone(repo_path, temp_base)

    try:
        # Checkout the historical commit in the temp clone
        subprocess.run(
            ["git", "-C", str(temp_clone), "checkout", "--quiet", "--force", commit],
            capture_output=True, check=True
        )

        # Measure LOC
        loc = run_tokei(temp_clone)
        return loc
    except subprocess.CalledProcessError:
        return {}


def load_history(history_file: Path) -> dict:
    """Load existing LOC history from file."""
    if history_file.exists():
        with open(history_file) as f:
            return json.load(f)
    return {"repos": {}, "last_updated": None}


def save_history(history: dict, history_file: Path):
    """Save LOC history to file."""
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)


def discover_repos(base_path: Path) -> list:
    """Find all git repositories in the base path."""
    repos = []
    for item in base_path.iterdir():
        if item.is_dir() and (item / ".git").exists():
            repos.append(item)
    return sorted(repos, key=lambda x: x.name.lower())


def accumulate_loc_history(base_path: Path, start_date: datetime, end_date: datetime, history_file: Path):
    """Accumulate LOC history for all repos in base_path."""
    history = load_history(history_file)
    repos = discover_repos(base_path)

    print(f"📊 Accumulating LOC history from {start_date.date()} to {end_date.date()}")
    print(f"   Found {len(repos)} repositories")

    # Generate list of dates to measure (daily snapshots)
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)  # Daily snapshots
    dates = sorted(set(dates))

    print(f"   Will measure {len(dates)} snapshots per repo")

    # Create a temporary directory for clones (avoids touching original repos)
    temp_base = Path(tempfile.mkdtemp(prefix="loc_history_"))
    print(f"   Using temp directory: {temp_base}")

    try:
        for repo_path in repos:
            repo_name = repo_path.name
            print(f"\n📁 {repo_name}")

            if repo_name not in history["repos"]:
                history["repos"][repo_name] = {"measurements": {}}

            repo_history = history["repos"][repo_name]
            repo_created = get_repo_created_date(repo_path)

            for date in dates:
                date_str = date.strftime("%Y-%m-%d")

                # Skip if we already have this measurement
                if date_str in repo_history["measurements"]:
                    print(f"   {date_str}: cached")
                    continue

                # Skip if date is before repo was created
                if date < repo_created:
                    print(f"   {date_str}: repo not yet created")
                    repo_history["measurements"][date_str] = {"total": 0, "languages": {}}
                    continue

                # Get commit at this date (from original repo)
                commit = get_commit_at_date(repo_path, date)
                if not commit:
                    print(f"   {date_str}: no commits yet")
                    repo_history["measurements"][date_str] = {"total": 0, "languages": {}}
                    continue

                # Measure LOC at this commit (using temp clone)
                loc = measure_loc_at_commit(repo_path, commit, temp_base)
                total = sum(loc.values())

                repo_history["measurements"][date_str] = {
                    "total": total,
                    "languages": loc,
                    "commit": commit[:8]
                }

                print(f"   {date_str}: {total:,} lines ({commit[:8]})")

            # Save after each repo (in case of interruption)
            save_history(history, history_file)

    finally:
        # Clean up temp directory
        print(f"\n🧹 Cleaning up temp directory...")
        shutil.rmtree(temp_base, ignore_errors=True)

    print(f"\n✅ LOC history saved to {history_file}")
    return history


def main():
    parser = argparse.ArgumentParser(description="Accumulate LOC history for dashboard")
    parser.add_argument("--path", required=True, help="Path to directory containing git repos")
    parser.add_argument("--days", type=int, default=1, help="Number of days to backfill (default: 1)")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default=LOC_HISTORY_FILE, help="Output file for LOC history")

    args = parser.parse_args()

    base_path = Path(args.path).expanduser().resolve()
    if not base_path.exists():
        print(f"❌ Path not found: {base_path}")
        return 1

    # Determine date range
    end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = end_date - timedelta(days=args.days)

    history_file = Path(args.output)

    accumulate_loc_history(base_path, start_date, end_date, history_file)
    return 0


if __name__ == "__main__":
    exit(main())
