# Vibe Dashboard

A personal development dashboard for tracking GitHub repositories, lines of code, commit history, releases, and project progress.

## Features

- **Lines of Code Tracking**: Accurate LOC counting using tokei, with language breakdown
- **Commit History**: 90-day commit activity chart with weekly trends
- **Codebase Growth**: 12-month stacked area chart showing LOC growth per repository
- **Language Distribution**: Pie chart of code by language (languages <3% grouped as "Other")
- **Releases Panel**: Recent releases across all repositories with version links
- **GitHub-style Calendar**: Contribution heatmap for the last 90 days
- **Fork Support**: Forks included in repos/commits/releases but excluded from LOC metrics
- **Shareable URLs**: Filter by repo, timeframe, or tab with URL parameters

## Quick Start

### View the Dashboard

Open `index.html` in your browser. The dashboard loads data from `dashboard_data.json`.

### Generate Your Own Data

#### Prerequisites

```bash
# Python dependencies
pip install requests python-dateutil

# Line counter (tokei recommended)
# macOS
brew install tokei

# Linux
cargo install tokei
# or download from https://github.com/XAMPPRocky/tokei/releases
```

#### Fetch Data from Local Repositories

```bash
# Scan all repos in a directory, filter by owner and author
python fetch_github_data.py \
  --local \
  --path /path/to/repos \
  --owner YourGitHubUsername \
  --author "Your Name"

# Exclude specific repos
python fetch_github_data.py \
  --local \
  --path /path/to/repos \
  --owner YourGitHubUsername \
  --exclude "repo1,repo2"

# Handle forks (included in commits/releases, excluded from LOC)
python fetch_github_data.py \
  --local \
  --path /path/to/repos \
  --owner YourGitHubUsername \
  --author "Your Name" \
  --fork-repos "forked-repo1,forked-repo2"
```

## Automated Updates with GitHub Actions

The included workflow (`.github/workflows/update-dashboard.yml`) automatically:

1. Clones all your repositories (including forks)
2. Counts lines of code using tokei
3. Extracts commit history filtered by author
4. Identifies releases/tags
5. Commits updated `dashboard_data.json` daily

To use it:
1. Fork or copy this repository
2. Update the workflow with your GitHub username
3. The workflow runs daily at 4 AM Pacific, or manually via "Run workflow"

## File Structure

```
├── index.html              # Main dashboard (loads dashboard_data.json)
├── fetch_github_data.py    # Data fetching script
├── dashboard_data.json     # Generated data file
├── projects_config.json    # Optional: manual project goals/progress
└── .github/workflows/
    └── update-dashboard.yml # Automated data updates
```

## Dashboard Tabs

### Overview
- Stats cards: Total LOC, commits, projects, weekly trend
- Commit activity chart (configurable: 30/60/90 days)
- Language distribution pie chart
- Codebase growth stacked area chart (configurable: 6/12 months)
- Project cards with progress indicators
- Recent releases panel

### Projects
- Sortable list of all repositories
- Details: commits, LOC breakdown, last commit date
- Links to GitHub repository pages

### Activity
- GitHub-style contribution calendar
- Recent project activity feed

## URL Parameters

Share specific views with URL parameters:

```
index.html?repo=my-project&tab=projects&days=30&months=6
```

| Parameter | Values | Description |
|-----------|--------|-------------|
| `repo` | repo name or `all` | Filter to specific repository |
| `tab` | `overview`, `projects`, `activity` | Active tab |
| `days` | `30`, `60`, `90` | Commit history timeframe |
| `months` | `6`, `12` | Codebase growth timeframe |

## Fork Handling

Forks are handled specially to show your contributions without inflating LOC metrics:

- **Included**: Repository list, commit counts (by author), releases
- **Excluded**: Lines of code, language stats, codebase growth chart

In the repo filter dropdown, forks appear at the bottom with a "Forks (no LOC)" separator.

## Customization

### Project Configuration

Edit `projects_config.json` to set custom goals and progress:

```json
{
  "username/repo-name": {
    "progress": 75,
    "goals": ["Feature A", "Feature B", "Documentation"],
    "completed_goals": ["Feature A"],
    "description": "Custom description"
  }
}
```

### Excluded Languages

By default, HTML and SVG are excluded from LOC counts. Modify the tokei section in `fetch_github_data.py` to change this:

```python
if lang not in ("Total", "HTML", "SVG") and isinstance(info, dict)
```

## Command Line Options

```
python fetch_github_data.py [options]

Options:
  --local              Scan local repositories (no API calls)
  --path PATH          Directory containing repositories
  --owner OWNER        Filter repos by GitHub owner
  --author AUTHOR      Filter commits by author name
  --exclude REPOS      Comma-separated repos to exclude entirely
  --fork-repos REPOS   Comma-separated repos that are forks (LOC excluded)
  --output FILE        Output file (default: dashboard_data.json)

GitHub API mode (alternative to --local):
  --user USERNAME      Fetch all repos for a GitHub user
  --repos REPO [...]   Fetch specific repos (format: owner/repo)
  --clone              Clone repos for accurate LOC counting
  --token TOKEN        GitHub API token
```

## Troubleshooting

### No data showing
- Ensure `dashboard_data.json` is in the same directory as `index.html`
- Check browser console (F12) for errors
- Verify the JSON file is valid

### Wrong commit counts
- Use `--author "Your Name"` to filter by author (especially important for forks)
- Check that the author name matches your git config

### LOC seems off
- Install tokei for accurate counting
- Check excluded languages in the script
- Forks have LOC excluded by design

## License

MIT
