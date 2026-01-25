# Vibe Dashboard

A development activity dashboard that visualizes commit history, lines of code, language distribution, and releases across GitHub repositories.

## Deployment

The dashboard is deployed via **GitHub Pages** with automated daily updates via **GitHub Actions**.

### GitHub Actions Workflow

The workflow (`.github/workflows/update-dashboard.yml`) runs:
- **Daily at 4 AM Pacific** (12 PM UTC)
- **On push to main branch**
- **Manually via workflow_dispatch**

The workflow:
1. Clones all non-fork, non-archived repos owned by Rick-Wilson
2. Installs tokei for lines of code counting
3. Runs `fetch_github_data.py` to generate `dashboard_data.json`
4. Commits and pushes the updated JSON file

### Live URL

Once GitHub Pages is enabled, the dashboard will be available at:
`https://rick-wilson.github.io/Vibe-Dashboard/`

## Running Locally

### Prerequisites

- Python 3.x
- tokei (for LOC counting): `brew install tokei` or `cargo install tokei`

### Quick Start

1. **Install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install python-dateutil requests
   ```

2. **Generate dashboard data:**
   ```bash
   python fetch_github_data.py --local --path /path/to/your/repos --owner YourGitHubUsername --author "Your Name"
   ```

3. **Start local server:**
   ```bash
   python3 -m http.server 8000
   ```

4. **Open in browser:**
   http://localhost:8000

### Script Options

```bash
python fetch_github_data.py --local --path /path/to/repos    # Scan local repos
python fetch_github_data.py --user username                   # Fetch via GitHub API
python fetch_github_data.py --repos owner/repo1 owner/repo2   # Specific repos

# Filters (for local mode)
--author "Name"     # Filter commits by author
--owner username    # Only repos owned by this user
--output file.json  # Output file (default: dashboard_data.json)
```

## Data Sources

- **Commits**: Extracted from git log
- **Lines of Code**: Counted by tokei (excludes HTML)
- **Releases**: Git tags with dates
- **Language Distribution**: From tokei analysis

## Key Files

- `index.html` - Dashboard UI (single-page app)
- `fetch_github_data.py` - Data collection script
- `dashboard_data.json` - Generated data file
- `.github/workflows/update-dashboard.yml` - Automation workflow
