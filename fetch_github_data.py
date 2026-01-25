#!/usr/bin/env python3
"""
BridgeDev Dashboard Data Fetcher
================================
Fetches repository data from GitHub or local git clones and generates JSON for the dashboard.

Requirements:
    pip install requests python-dateutil

Optional (for lines of code counting):
    - Install 'scc': https://github.com/boyter/scc
    - Or install 'tokei': cargo install tokei
    - Or install 'cloc': apt install cloc / brew install cloc

Usage:
    # Fetch from local repositories (no API calls needed)
    python fetch_github_data.py --local --path /path/to/repos

    # Set your GitHub token (optional but recommended for higher rate limits)
    export GITHUB_TOKEN="your_token_here"

    # Run the script
    python fetch_github_data.py

    # Or specify repos directly
    python fetch_github_data.py --repos owner/repo1 owner/repo2

    # Fetch all repos for a user
    python fetch_github_data.py --user yourusername
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import requests
from dateutil import parser as date_parser


# Configuration
CONFIG = {
    "output_file": "dashboard_data.json",
    "clone_dir": ".repos_cache",  # Where to clone repos for LOC counting
    "github_api": "https://api.github.com",
    "loc_tool": "tokei",  # Options: 'scc', 'tokei', 'cloc', or None to skip
}


class GitHubFetcher:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"token {self.token}"
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "BridgeDev-Dashboard"
    
    def _get(self, endpoint: str, params: dict = None) -> dict:
        """Make a GET request to the GitHub API."""
        url = f"{CONFIG['github_api']}{endpoint}"
        response = self.session.get(url, params=params)
        
        if response.status_code == 403:
            reset_time = response.headers.get('X-RateLimit-Reset')
            if reset_time:
                reset_dt = datetime.fromtimestamp(int(reset_time))
                print(f"⚠️  Rate limited. Resets at {reset_dt}")
            raise Exception(f"GitHub API rate limit exceeded")
        
        response.raise_for_status()
        return response.json()
    
    def _get_paginated(self, endpoint: str, params: dict = None, max_pages: int = 10) -> list:
        """Get paginated results from GitHub API."""
        params = params or {}
        params["per_page"] = 100
        all_results = []
        
        for page in range(1, max_pages + 1):
            params["page"] = page
            results = self._get(endpoint, params)
            if not results:
                break
            all_results.extend(results)
            if len(results) < 100:
                break
        
        return all_results
    
    def get_user_repos(self, username: str) -> list:
        """Get all repositories for a user."""
        print(f"📦 Fetching repositories for {username}...")
        repos = self._get_paginated(f"/users/{username}/repos", {"type": "owner", "sort": "updated"})
        print(f"   Found {len(repos)} repositories")
        return repos
    
    def get_repo_info(self, owner: str, repo: str) -> dict:
        """Get detailed information about a repository."""
        return self._get(f"/repos/{owner}/{repo}")
    
    def get_repo_languages(self, owner: str, repo: str) -> dict:
        """Get language breakdown for a repository (in bytes)."""
        return self._get(f"/repos/{owner}/{repo}/languages")
    
    def get_repo_commits(self, owner: str, repo: str, since: datetime = None) -> list:
        """Get commits for a repository."""
        params = {}
        if since:
            params["since"] = since.isoformat()
        return self._get_paginated(f"/repos/{owner}/{repo}/commits", params)
    
    def get_commit_activity(self, owner: str, repo: str) -> list:
        """Get weekly commit activity for the last year."""
        try:
            return self._get(f"/repos/{owner}/{repo}/stats/commit_activity")
        except:
            return []
    
    def get_code_frequency(self, owner: str, repo: str) -> list:
        """Get weekly additions/deletions."""
        try:
            return self._get(f"/repos/{owner}/{repo}/stats/code_frequency")
        except:
            return []


class LocalRepoScanner:
    """Scan local git repositories for dashboard data without API calls."""

    def __init__(self, base_path: str, author: str = None):
        self.base_path = Path(base_path)
        self.author = author

    def discover_repos(self) -> list:
        """Find all git repositories in the base path."""
        repos = []
        for item in self.base_path.iterdir():
            if item.is_dir() and (item / ".git").exists():
                repos.append(item)
        return sorted(repos, key=lambda x: x.name.lower())

    def get_repo_info(self, repo_path: Path) -> dict:
        """Extract basic info from a local repository."""
        name = repo_path.name

        # Get remote URL to extract owner
        remote_url = self._run_git(repo_path, ["remote", "get-url", "origin"])
        owner = self._parse_owner_from_url(remote_url) if remote_url else "local"

        # Get first commit date (created_at)
        first_commit = self._run_git(repo_path, ["log", "--reverse", "--format=%aI", "--max-count=1"])

        # Get last commit date
        last_commit = self._run_git(repo_path, ["log", "-1", "--format=%aI"])

        # Get description from .git/description or README
        description = self._get_description(repo_path)

        return {
            "name": name,
            "full_name": f"{owner}/{name}",
            "owner": owner,
            "description": description or f"Repository: {name}",
            "created_at": first_commit.strip() if first_commit else "",
            "updated_at": last_commit.strip() if last_commit else "",
            "pushed_at": last_commit.strip() if last_commit else "",
            "html_url": remote_url.strip().replace(".git", "") if remote_url else "",
        }

    def get_commit_count(self, repo_path: Path) -> int:
        """Get total number of commits."""
        cmd = ["rev-list", "--count", "HEAD"]
        if self.author:
            cmd = ["log", "--oneline"]
            cmd.append(f"--author={self.author}")
            result = self._run_git(repo_path, cmd)
            return len(result.strip().split("\n")) if result and result.strip() else 0
        result = self._run_git(repo_path, cmd)
        return int(result.strip()) if result else 0

    def get_commits_since(self, repo_path: Path, since: datetime) -> list:
        """Get commits since a given date."""
        since_str = since.strftime("%Y-%m-%d")
        cmd = ["log", f"--since={since_str}", "--format=%H|%aI|%s"]
        if self.author:
            cmd.append(f"--author={self.author}")
        result = self._run_git(repo_path, cmd)
        if not result:
            return []

        commits = []
        for line in result.strip().split("\n"):
            if line and "|" in line:
                parts = line.split("|", 2)
                if len(parts) >= 2:
                    commits.append({
                        "sha": parts[0],
                        "commit": {
                            "author": {"date": parts[1]},
                            "message": parts[2] if len(parts) > 2 else ""
                        }
                    })
        return commits

    def get_code_frequency(self, repo_path: Path, weeks: int = 12) -> list:
        """Get weekly additions/deletions for the last N weeks."""
        result = []
        today = datetime.now()

        for i in range(weeks - 1, -1, -1):
            week_start = today - timedelta(days=(i + 1) * 7)
            week_end = today - timedelta(days=i * 7)

            cmd = [
                "log",
                f"--since={week_start.strftime('%Y-%m-%d')}",
                f"--until={week_end.strftime('%Y-%m-%d')}",
                "--numstat",
                "--format="
            ]
            if self.author:
                cmd.append(f"--author={self.author}")
            stats = self._run_git(repo_path, cmd)

            additions = 0
            deletions = 0
            if stats:
                for line in stats.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            if parts[0] != "-":
                                additions += int(parts[0])
                            if parts[1] != "-":
                                deletions += int(parts[1])
                        except ValueError:
                            pass

            result.append([
                int(week_start.timestamp()),
                additions,
                -deletions
            ])

        return result

    def get_monthly_loc_changes(self, repo_path: Path, months: int = 12) -> list:
        """Get monthly additions/deletions for LOC history using actual calendar months."""
        from calendar import monthrange
        result = []
        today = datetime.now()

        # Generate list of months going back
        for i in range(months - 1, -1, -1):
            # Calculate the target month
            year = today.year
            month = today.month - i
            while month <= 0:
                month += 12
                year -= 1

            # First and last day of the month
            first_day = datetime(year, month, 1)
            last_day_num = monthrange(year, month)[1]
            last_day = datetime(year, month, last_day_num, 23, 59, 59)

            cmd = [
                "log",
                f"--since={first_day.strftime('%Y-%m-%d')}",
                f"--until={last_day.strftime('%Y-%m-%d')}",
                "--numstat",
                "--format="
            ]
            if self.author:
                cmd.append(f"--author={self.author}")
            stats = self._run_git(repo_path, cmd)

            additions = 0
            deletions = 0
            if stats:
                for line in stats.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            if parts[0] != "-":
                                additions += int(parts[0])
                            if parts[1] != "-":
                                deletions += int(parts[1])
                        except ValueError:
                            pass

            result.append({
                "month": first_day.strftime("%b %Y"),
                "month_short": first_day.strftime("%b"),
                "additions": additions,
                "deletions": deletions,
                "net": additions - deletions
            })

        return result

    def get_releases(self, repo_path: Path, limit: int = 10) -> list:
        """Get releases (git tags) with dates, sorted by date descending."""
        # Get tags with their dates using for-each-ref
        result = self._run_git(repo_path, [
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)|%(creatordate:iso-strict)|%(subject)",
            "refs/tags",
            f"--count={limit}"
        ])

        if not result:
            return []

        releases = []
        repo_name = repo_path.name
        for line in result.strip().split("\n"):
            if line and "|" in line:
                parts = line.split("|", 2)
                if len(parts) >= 2:
                    tag = parts[0]
                    date = parts[1]
                    message = parts[2] if len(parts) > 2 else ""
                    releases.append({
                        "tag": tag,
                        "date": date,
                        "message": message,
                        "repo": repo_name
                    })

        return releases

    def _run_git(self, repo_path: Path, args: list) -> Optional[str]:
        """Run a git command and return output."""
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path)] + args,
                capture_output=True, text=True, timeout=30
            )
            return result.stdout if result.returncode == 0 else None
        except Exception:
            return None

    def _parse_owner_from_url(self, url: str) -> str:
        """Extract owner from git remote URL."""
        url = url.strip()
        # Handle SSH URLs: git@github.com:owner/repo.git
        if url.startswith("git@"):
            match = url.split(":")[-1]
            if "/" in match:
                return match.split("/")[0]
        # Handle HTTPS URLs: https://github.com/owner/repo.git
        elif "github.com" in url:
            parts = url.split("github.com")[-1].strip("/").split("/")
            if parts:
                return parts[0]
        return "local"

    def _get_description(self, repo_path: Path) -> str:
        """Try to get repository description."""
        # Check .git/description
        desc_file = repo_path / ".git" / "description"
        if desc_file.exists():
            content = desc_file.read_text().strip()
            if content and "Unnamed repository" not in content:
                return content

        # Check README for first line
        for readme in ["README.md", "README.rst", "README.txt", "README"]:
            readme_path = repo_path / readme
            if readme_path.exists():
                try:
                    lines = readme_path.read_text().split("\n")
                    for line in lines[:5]:
                        line = line.strip().lstrip("#").strip()
                        if line and len(line) > 5:
                            return line[:200]
                except Exception:
                    pass

        return ""


def fetch_local_project_data(scanner: LocalRepoScanner, repo_path: Path, skip_loc: bool = False) -> dict:
    """Fetch all data for a single local project."""
    fork_indicator = " (fork)" if skip_loc else ""
    print(f"\n📊 Processing {repo_path.name}{fork_indicator}...")

    # Basic repo info
    info = scanner.get_repo_info(repo_path)

    # Get LOC using configured tool (skip for forks)
    if skip_loc:
        loc = {}
        print(f"   ⏭️  Skipping LOC count (fork)")
    else:
        loc = count_lines_of_code(str(repo_path), CONFIG["loc_tool"])

    # Commits (last 90 days)
    since = datetime.now() - timedelta(days=90)
    commits = scanner.get_commits_since(repo_path, since)
    commit_history = process_commit_history(commits)

    # Total commits
    total_commits = scanner.get_commit_count(repo_path)

    # Code frequency (additions/deletions)
    code_freq = scanner.get_code_frequency(repo_path)
    code_changes = process_code_frequency(code_freq) if code_freq else []

    # Monthly LOC changes for history
    monthly_loc_changes = scanner.get_monthly_loc_changes(repo_path)

    # Get releases (tags)
    releases = scanner.get_releases(repo_path)

    # Determine primary language from LOC
    primary_language = max(loc.keys(), key=lambda k: loc[k]) if loc else "Unknown"

    project = {
        "id": hash(info["full_name"]) & 0xFFFFFFFF,  # Generate a stable ID
        "name": info["name"],
        "full_name": info["full_name"],
        "description": info["description"],
        "language": primary_language,
        "stars": 0,  # Not available locally
        "forks": 0,  # Not available locally
        "open_issues": 0,  # Not available locally
        "loc": loc,
        "commits": total_commits,
        "recent_commits": sum(d["commits"] for d in commit_history),
        "last_commit": info.get("pushed_at", ""),
        "created_at": info.get("created_at", ""),
        "updated_at": info.get("updated_at", ""),
        "url": info.get("html_url", ""),
        "commit_history": commit_history,
        "code_changes": code_changes,
        "monthly_loc_changes": monthly_loc_changes,
        "releases": releases,
        "progress": 0,
        "goals": [],
        "completed_goals": [],
    }

    loc_summary = f"{sum(loc.values())} lines of code" if loc else "LOC excluded"
    print(f"   ✓ {total_commits} commits, {loc_summary}")
    return project


def count_lines_of_code(repo_path: str, tool: str = "scc") -> dict:
    """
    Count lines of code using scc, tokei, or cloc.
    Returns a dict of {language: lines}.
    """
    if not tool:
        return {}
    
    try:
        if tool == "scc":
            result = subprocess.run(
                ["scc", "--format", "json", repo_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return {item["Name"]: item["Code"] for item in data if item.get("Code", 0) > 0}
        
        elif tool == "tokei":
            result = subprocess.run(
                ["tokei", "--output", "json", repo_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return {lang: info.get("code", 0) for lang, info in data.items()
                        if lang not in ("Total", "HTML", "SVG") and isinstance(info, dict) and info.get("code", 0) > 0}
        
        elif tool == "cloc":
            result = subprocess.run(
                ["cloc", "--json", repo_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return {lang: info.get("code", 0) for lang, info in data.items()
                        if lang not in ("header", "SUM", "SVG") and isinstance(info, dict)}
    
    except FileNotFoundError:
        print(f"   ⚠️  {tool} not found. Install it for accurate LOC counting.")
    except subprocess.TimeoutExpired:
        print(f"   ⚠️  LOC counting timed out for {repo_path}")
    except Exception as e:
        print(f"   ⚠️  Error counting LOC: {e}")
    
    return {}


def clone_or_update_repo(owner: str, repo: str, clone_dir: str) -> Optional[str]:
    """Clone or update a repository for LOC counting."""
    repo_path = Path(clone_dir) / f"{owner}_{repo}"
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    
    clone_url = f"https://github.com/{owner}/{repo}.git"
    
    try:
        if repo_path.exists():
            print(f"   📥 Updating {owner}/{repo}...")
            subprocess.run(
                ["git", "-C", str(repo_path), "pull", "--quiet"],
                capture_output=True, timeout=120
            )
        else:
            print(f"   📥 Cloning {owner}/{repo}...")
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", clone_url, str(repo_path)],
                capture_output=True, timeout=120
            )
        return str(repo_path)
    except Exception as e:
        print(f"   ⚠️  Failed to clone/update {owner}/{repo}: {e}")
        return None


def estimate_loc_from_languages(languages: dict) -> dict:
    """
    Estimate lines of code from GitHub's language bytes.
    This is a rough estimate (assumes ~50 bytes per line on average).
    """
    bytes_per_line = {
        "Python": 35,
        "JavaScript": 40,
        "TypeScript": 45,
        "Java": 50,
        "C#": 50,
        "C": 40,
        "C++": 45,
        "Go": 35,
        "Rust": 45,
        "Ruby": 30,
        "PHP": 40,
        "HTML": 60,
        "CSS": 45,
        "Markdown": 50,
    }
    default_bpl = 45
    
    return {
        lang: max(1, bytes // bytes_per_line.get(lang, default_bpl))
        for lang, bytes in languages.items()
        if bytes > 0
    }


def process_commit_history(commits: list, days: int = 90) -> list:
    """Process commits into daily activity data."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = today - timedelta(days=days)
    
    # Initialize all days
    daily_commits = {}
    for i in range(days):
        date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        daily_commits[date] = {"date": date, "commits": 0, "additions": 0, "deletions": 0}
    
    # Count commits per day
    for commit in commits:
        try:
            commit_date = commit.get("commit", {}).get("author", {}).get("date", "")
            if commit_date:
                dt = date_parser.parse(commit_date).strftime("%Y-%m-%d")
                if dt in daily_commits:
                    daily_commits[dt]["commits"] += 1
        except:
            pass
    
    return list(daily_commits.values())


def process_code_frequency(code_freq: list) -> list:
    """Process code frequency data into weekly additions/deletions."""
    result = []
    for week in code_freq[-12:]:  # Last 12 weeks
        if len(week) >= 3:
            timestamp, additions, deletions = week[0], week[1], abs(week[2])
            date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
            result.append({"date": date, "additions": additions, "deletions": deletions})
    return result


def fetch_project_data(fetcher: GitHubFetcher, owner: str, repo: str, 
                       clone_for_loc: bool = False) -> dict:
    """Fetch all data for a single project."""
    print(f"\n📊 Processing {owner}/{repo}...")
    
    # Basic repo info
    info = fetcher.get_repo_info(owner, repo)
    
    # Languages
    languages_bytes = fetcher.get_repo_languages(owner, repo)
    
    # Get LOC - either by cloning or estimating
    if clone_for_loc and CONFIG["loc_tool"]:
        repo_path = clone_or_update_repo(owner, repo, CONFIG["clone_dir"])
        if repo_path:
            loc = count_lines_of_code(repo_path, CONFIG["loc_tool"])
        else:
            loc = estimate_loc_from_languages(languages_bytes)
    else:
        loc = estimate_loc_from_languages(languages_bytes)
    
    # Commits (last 90 days)
    since = datetime.now() - timedelta(days=90)
    commits = fetcher.get_repo_commits(owner, repo, since)
    commit_history = process_commit_history(commits)
    
    # Code frequency (additions/deletions)
    code_freq = fetcher.get_code_frequency(owner, repo)
    code_changes = process_code_frequency(code_freq) if code_freq else []
    
    # Determine primary language
    primary_language = info.get("language") or (max(loc.keys(), key=lambda k: loc[k]) if loc else "Unknown")
    
    # Calculate total commits
    total_commits = len(fetcher.get_repo_commits(owner, repo))
    
    project = {
        "id": info["id"],
        "name": info["name"],
        "full_name": info["full_name"],
        "description": info.get("description") or f"Repository: {repo}",
        "language": primary_language,
        "stars": info.get("stargazers_count", 0),
        "forks": info.get("forks_count", 0),
        "open_issues": info.get("open_issues_count", 0),
        "loc": loc,
        "commits": total_commits,
        "recent_commits": sum(d["commits"] for d in commit_history),
        "last_commit": info.get("pushed_at", ""),
        "created_at": info.get("created_at", ""),
        "updated_at": info.get("updated_at", ""),
        "url": info.get("html_url", ""),
        "commit_history": commit_history,
        "code_changes": code_changes,
        # These would need to be manually configured or fetched from GitHub Projects
        "progress": 0,  # Manual or calculated
        "goals": [],
        "completed_goals": [],
    }
    
    print(f"   ✓ {total_commits} commits, {sum(loc.values())} lines of code")
    return project


def calculate_progress(project: dict) -> int:
    """
    Calculate estimated progress based on various metrics.
    This is a heuristic - you may want to manually set progress per project.
    """
    # Simple heuristic based on:
    # - Commit activity (more recent = more active)
    # - Open issues (fewer = more complete)
    # - Code size (larger codebase may indicate maturity)
    
    recent_commits = project.get("recent_commits", 0)
    open_issues = project.get("open_issues", 0)
    total_loc = sum(project.get("loc", {}).values())
    
    # Base progress from code size (max 40 points)
    if total_loc > 10000:
        code_score = 40
    elif total_loc > 5000:
        code_score = 30
    elif total_loc > 1000:
        code_score = 20
    else:
        code_score = 10
    
    # Activity score (max 30 points)
    if recent_commits > 50:
        activity_score = 30
    elif recent_commits > 20:
        activity_score = 20
    elif recent_commits > 5:
        activity_score = 10
    else:
        activity_score = 5
    
    # Issue score (max 30 points) - fewer open issues = higher score
    if open_issues == 0:
        issue_score = 30
    elif open_issues < 5:
        issue_score = 20
    elif open_issues < 10:
        issue_score = 10
    else:
        issue_score = 5
    
    return min(100, code_score + activity_score + issue_score)


def aggregate_commit_history(projects: list) -> list:
    """Aggregate commit history across all projects."""
    combined = {}
    
    for project in projects:
        for day in project.get("commit_history", []):
            date = day["date"]
            if date not in combined:
                combined[date] = {"date": date, "commits": 0, "additions": 0, "deletions": 0}
            combined[date]["commits"] += day.get("commits", 0)
            combined[date]["additions"] += day.get("additions", 0)
            combined[date]["deletions"] += day.get("deletions", 0)
    
    return sorted(combined.values(), key=lambda x: x["date"])


def generate_loc_history(projects: list) -> dict:
    """
    Generate LOC growth history based on actual monthly git data.
    Works backwards from current LOC using real additions/deletions.
    Returns both aggregated history and per-repo breakdown.
    Excludes forks from LOC calculations.
    """
    from calendar import monthrange
    # Exclude forks from LOC calculations
    non_fork_projects = [p for p in projects if not p.get("is_fork", False)]
    total_loc = sum(sum(p.get("loc", {}).values()) for p in non_fork_projects)
    today = datetime.now()

    # Build month labels for the last 12 calendar months
    months_data = []
    for i in range(11, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        months_data.append({
            "month": datetime(year, month, 1).strftime("%b"),
            "year": year,
            "month_num": month,
            "net": 0
        })

    # Calculate per-project LOC history (excluding forks)
    repos_history = []
    for p in non_fork_projects:
        project_loc = sum(p.get("loc", {}).values())
        changes = p.get("monthly_loc_changes", [])

        # Find first month with any commit activity (additions or deletions > 0)
        first_active_month = None
        for idx, change in enumerate(changes):
            if change.get("additions", 0) > 0 or change.get("deletions", 0) > 0:
                first_active_month = idx
                break

        # Work forwards: start at 0, accumulate net changes, end at current LOC
        loc_values = [0] * 12

        if first_active_month is not None:
            # Calculate total net change from first active month to now
            total_net = sum(c.get("net", 0) for c in changes[first_active_month:])

            # Starting LOC = current LOC - all net changes since first activity
            starting_loc = max(0, project_loc - total_net)

            # Build forward from first active month
            running = starting_loc
            for i in range(first_active_month, 12):
                if i < len(changes):
                    running += changes[i].get("net", 0)
                loc_values[i] = max(0, running)

            # Ensure final value matches current LOC
            loc_values[11] = project_loc

        repos_history.append({
            "name": p.get("name", "Unknown"),
            "data": loc_values
        })

    # Aggregate monthly net changes across non-fork projects
    for p in non_fork_projects:
        changes = p.get("monthly_loc_changes", [])
        project_loc = sum(p.get("loc", {}).values())
        for idx, change in enumerate(changes):
            if idx < len(months_data):
                # Cap net change to reasonable bounds (avoid HTML inflation)
                net = change.get("net", 0)
                # Limit to project's current LOC to avoid impossible values
                capped_net = max(-project_loc, min(net, project_loc))
                months_data[idx]["net"] += capped_net

    # Calculate cumulative LOC working backwards from current total
    history = []
    running_loc = total_loc

    # Start from the end (current month) and work backwards
    loc_values = [0] * 12
    loc_values[11] = total_loc

    for i in range(10, -1, -1):
        # Subtract the net change of the next month to get this month's ending LOC
        running_loc -= months_data[i + 1]["net"]
        # Floor at 10% of current LOC (there was some code before we started tracking)
        loc_values[i] = max(int(total_loc * 0.1), running_loc)

    # Build the history list
    for i in range(12):
        history.append({
            "month": months_data[i]["month"],
            "loc": loc_values[i]
        })

    # Sort repos by current LOC (largest first) for better chart stacking
    repos_history.sort(key=lambda x: x["data"][-1], reverse=True)

    return {
        "months": [m["month"] for m in months_data],
        "total": history,
        "repos": repos_history
    }


def load_project_config(config_file: str = "projects_config.json") -> dict:
    """Load manual project configuration (goals, progress, etc.)."""
    if Path(config_file).exists():
        with open(config_file) as f:
            return json.load(f)
    return {}


def save_project_config_template(projects: list, config_file: str = "projects_config.json"):
    """Save a template config file for manual project configuration."""
    if Path(config_file).exists():
        return  # Don't overwrite existing config
    
    config = {}
    for p in projects:
        config[p["full_name"]] = {
            "progress": p.get("progress", 50),
            "goals": ["Goal 1", "Goal 2", "Goal 3"],
            "completed_goals": ["Goal 1"],
            "description": p.get("description", ""),
        }
    
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
    
    print(f"\n📝 Created {config_file} - edit this file to set goals and progress manually")


def load_todos(todos_file: str = "todos.json") -> list:
    """Load todos from file."""
    if Path(todos_file).exists():
        with open(todos_file) as f:
            return json.load(f)
    return []


def save_todos_template(projects: list, todos_file: str = "todos.json"):
    """Save a template todos file."""
    if Path(todos_file).exists():
        return
    
    todos = [
        {
            "id": 1,
            "text": "Example task - edit todos.json to customize",
            "project": projects[0]["name"] if projects else "general",
            "priority": "medium",
            "done": False
        }
    ]
    
    with open(todos_file, "w") as f:
        json.dump(todos, f, indent=2)
    
    print(f"📝 Created {todos_file} - edit this file to manage your todos")


def main():
    parser = argparse.ArgumentParser(description="Fetch GitHub data for BridgeDev Dashboard")
    parser.add_argument("--repos", nargs="+", help="Specific repos to fetch (format: owner/repo)")
    parser.add_argument("--user", help="Fetch all repos for a GitHub user")
    parser.add_argument("--local", action="store_true", help="Scan local repositories instead of GitHub API")
    parser.add_argument("--path", help="Path to directory containing local repositories (used with --local)")
    parser.add_argument("--author", help="Filter commits by author name or email (used with --local)")
    parser.add_argument("--owner", help="Only include repos owned by this GitHub user (used with --local)")
    parser.add_argument("--exclude", help="Comma-separated list of repo names to exclude (used with --local)")
    parser.add_argument("--fork-repos", help="Comma-separated list of repo names that are forks (LOC excluded but included in other metrics)")
    parser.add_argument("--clone", action="store_true", help="Clone repos for accurate LOC counting")
    parser.add_argument("--output", default=CONFIG["output_file"], help="Output JSON file")
    parser.add_argument("--token", help="GitHub token (or set GITHUB_TOKEN env var)")
    args = parser.parse_args()

    # Load manual configuration
    project_config = load_project_config()
    projects = []

    if args.local:
        # Local mode - scan local repositories
        local_path = args.path or os.path.dirname(os.getcwd())
        if not Path(local_path).exists():
            print(f"❌ Path does not exist: {local_path}")
            sys.exit(1)

        print(f"📂 Scanning local repositories in: {local_path}")
        if args.author:
            print(f"📝 Filtering commits by author: {args.author}")
        if args.owner:
            print(f"📁 Filtering repos by owner: {args.owner}")
        fork_repos = set()
        if args.fork_repos:
            fork_repos = {x.strip().lower() for x in args.fork_repos.split(",") if x.strip()}
            print(f"🍴 Fork repos (LOC excluded): {', '.join(fork_repos) if fork_repos else 'none'}")
        scanner = LocalRepoScanner(local_path, author=args.author)
        repos = scanner.discover_repos()

        # Filter by owner if specified
        if args.owner:
            filtered_repos = []
            for repo_path in repos:
                info = scanner.get_repo_info(repo_path)
                if info.get("owner", "").lower() == args.owner.lower():
                    filtered_repos.append(repo_path)
            repos = filtered_repos

        # Filter out excluded repos
        if args.exclude:
            excluded = [x.strip().lower() for x in args.exclude.split(",")]
            print(f"🚫 Excluding repos: {', '.join(excluded)}")
            repos = [r for r in repos if r.name.lower() not in excluded]

        if not repos:
            print("❌ No git repositories found in the specified path")
            sys.exit(1)

        print(f"\n🚀 Processing {len(repos)} local repositories...")

        for repo_path in repos:
            try:
                is_fork = repo_path.name.lower() in fork_repos
                project = fetch_local_project_data(scanner, repo_path, skip_loc=is_fork)
                project["is_fork"] = is_fork

                # Apply manual configuration
                if project["full_name"] in project_config:
                    config = project_config[project["full_name"]]
                    project["progress"] = config.get("progress", project["progress"])
                    project["goals"] = config.get("goals", project["goals"])
                    project["completed_goals"] = config.get("completed_goals", project["completed_goals"])
                    if config.get("description"):
                        project["description"] = config["description"]

                # Calculate progress if not manually set
                if project["progress"] == 0:
                    project["progress"] = calculate_progress(project)

                projects.append(project)

            except Exception as e:
                print(f"   ❌ Error processing {repo_path.name}: {e}")

    else:
        # GitHub API mode
        token = args.token or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("⚠️  No GitHub token provided. API rate limits will be stricter.")
            print("   Set GITHUB_TOKEN environment variable or use --token flag")

        fetcher = GitHubFetcher(token)

        # Determine which repos to fetch
        repos_to_fetch = []

        if args.repos:
            for repo in args.repos:
                if "/" in repo:
                    owner, name = repo.split("/", 1)
                    repos_to_fetch.append((owner, name))
                else:
                    print(f"⚠️  Invalid repo format: {repo}. Use owner/repo format.")

        elif args.user:
            user_repos = fetcher.get_user_repos(args.user)
            repos_to_fetch = [(args.user, repo["name"]) for repo in user_repos if not repo.get("fork")]

        else:
            print("❌ Please specify --repos, --user, or --local")
            print("\nExamples:")
            print("  python fetch_github_data.py --local --path /path/to/repos")
            print("  python fetch_github_data.py --user yourusername")
            print("  python fetch_github_data.py --repos owner/repo1 owner/repo2")
            sys.exit(1)

        if not repos_to_fetch:
            print("❌ No repositories found to process")
            sys.exit(1)

        print(f"\n🚀 Processing {len(repos_to_fetch)} repositories...")

        for owner, repo in repos_to_fetch:
            try:
                project = fetch_project_data(fetcher, owner, repo, clone_for_loc=args.clone)

                # Apply manual configuration
                if project["full_name"] in project_config:
                    config = project_config[project["full_name"]]
                    project["progress"] = config.get("progress", project["progress"])
                    project["goals"] = config.get("goals", project["goals"])
                    project["completed_goals"] = config.get("completed_goals", project["completed_goals"])
                    if config.get("description"):
                        project["description"] = config["description"]

                # Calculate progress if not manually set
                if project["progress"] == 0:
                    project["progress"] = calculate_progress(project)

                projects.append(project)

            except Exception as e:
                print(f"   ❌ Error processing {owner}/{repo}: {e}")
    
    if not projects:
        print("❌ No projects were successfully processed")
        sys.exit(1)
    
    # Calculate aggregate statistics (exclude fork LOC)
    non_fork_projects = [p for p in projects if not p.get("is_fork", False)]
    total_loc = sum(sum(p.get("loc", {}).values()) for p in non_fork_projects)
    total_commits = sum(p.get("commits", 0) for p in projects)  # Include all commits
    avg_progress = sum(p.get("progress", 0) for p in projects) // len(projects)
    
    # Aggregate commit history
    commit_history = aggregate_commit_history(projects)
    
    # Calculate week-over-week trend
    this_week = sum(d["commits"] for d in commit_history[-7:])
    last_week = sum(d["commits"] for d in commit_history[-14:-7])
    week_trend = this_week - last_week
    
    # Generate LOC history
    loc_history = generate_loc_history(projects)
    
    # Language breakdown (exclude forks)
    language_totals = {}
    for p in non_fork_projects:
        for lang, lines in p.get("loc", {}).items():
            language_totals[lang] = language_totals.get(lang, 0) + lines
    
    # Load todos
    todos = load_todos()

    # Aggregate releases from all projects, sorted by date
    all_releases = []
    for p in projects:
        all_releases.extend(p.get("releases", []))
    # Sort by date descending
    all_releases.sort(key=lambda r: r.get("date", ""), reverse=True)

    # Build final output
    output = {
        "generated_at": datetime.now().isoformat(),
        "stats": {
            "total_loc": total_loc,
            "total_commits": total_commits,
            "avg_progress": avg_progress,
            "this_week_commits": this_week,
            "last_week_commits": last_week,
            "week_trend": week_trend,
            "project_count": len(projects),
            "fork_count": len(projects) - len(non_fork_projects),
        },
        "languages": language_totals,
        "commit_history": commit_history,
        "loc_history": loc_history["total"],
        "loc_history_by_repo": {
            "months": loc_history["months"],
            "repos": loc_history["repos"]
        },
        "projects": projects,
        "releases": all_releases,
        "todos": todos,
    }
    
    # Write output
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✅ Dashboard data saved to {args.output}")
    fork_count = len(projects) - len(non_fork_projects)
    print(f"\n📈 Summary:")
    print(f"   Projects: {len(projects)} ({fork_count} forks)")
    print(f"   Total LOC: {total_loc:,} (excluding forks)")
    print(f"   Total Commits: {total_commits:,}")
    print(f"   Avg Progress: {avg_progress}%")
    print(f"   This Week: {this_week} commits ({'+' if week_trend >= 0 else ''}{week_trend} vs last week)")
    
    # Save template files
    save_project_config_template(projects)
    save_todos_template(projects)
    
    print(f"\n💡 Next steps:")
    print(f"   1. Edit projects_config.json to set goals and progress for each project")
    print(f"   2. Edit todos.json to manage your task list")
    print(f"   3. Run this script periodically to update dashboard_data.json")
    print(f"   4. Update the HTML dashboard to load from dashboard_data.json")


if __name__ == "__main__":
    main()
