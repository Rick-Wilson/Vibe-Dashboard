#!/usr/bin/env python3
"""
Parse Claude Code session files to extract user prompt statistics.
Generates claude_stats.json with user prompt counts instead of total messages.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
STATS_CACHE = CLAUDE_DIR / "stats-cache.json"

def parse_session_file(filepath):
    """Parse a single session .jsonl file and count user prompts."""
    user_prompts = []

    try:
        with open(filepath, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    # User prompts have type="user" and userType="external"
                    # and message.content with text (not tool_result)
                    if (entry.get('type') == 'user' and
                        entry.get('userType') == 'external' and
                        entry.get('message', {}).get('role') == 'user'):

                        content = entry.get('message', {}).get('content', [])
                        # Check if it's actual user text, not a tool result
                        if isinstance(content, list):
                            has_text = any(c.get('type') == 'text' for c in content)
                            has_tool_result = any(c.get('type') == 'tool_result' for c in content)
                            if has_text and not has_tool_result:
                                timestamp = entry.get('timestamp')
                                if timestamp:
                                    user_prompts.append(timestamp)
                        elif isinstance(content, str) and content.strip():
                            timestamp = entry.get('timestamp')
                            if timestamp:
                                user_prompts.append(timestamp)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error reading {filepath}: {e}")

    return user_prompts

def main():
    # Load existing stats-cache for base data
    if STATS_CACHE.exists():
        with open(STATS_CACHE) as f:
            stats = json.load(f)
    else:
        stats = {}

    # Collect all user prompts with timestamps
    all_prompts = []

    # Scan all project directories
    if PROJECTS_DIR.exists():
        for project_dir in PROJECTS_DIR.iterdir():
            if project_dir.is_dir():
                for session_file in project_dir.glob("*.jsonl"):
                    prompts = parse_session_file(session_file)
                    all_prompts.extend(prompts)

    # Group by date (convert UTC to local time)
    prompts_by_date = defaultdict(int)
    prompts_by_hour = defaultdict(int)

    for ts in all_prompts:
        try:
            # Parse UTC timestamp and convert to local time
            dt_utc = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            dt_local = dt_utc.astimezone()  # Convert to local timezone
            date_str = dt_local.strftime('%Y-%m-%d')
            hour = dt_local.hour
            prompts_by_date[date_str] += 1
            prompts_by_hour[hour] += 1
        except:
            continue

    # Build daily activity with user prompts
    daily_activity = []
    for date_str in sorted(prompts_by_date.keys()):
        # Find matching entry in original stats for session/tool counts
        original = next(
            (d for d in stats.get('dailyActivity', []) if d.get('date') == date_str),
            {}
        )
        daily_activity.append({
            'date': date_str,
            'userPrompts': prompts_by_date[date_str],
            'sessionCount': original.get('sessionCount', 0),
            'toolCallCount': original.get('toolCallCount', 0),
            'messageCount': original.get('messageCount', 0)  # Keep for reference
        })

    # Build output
    output = {
        'version': 1,
        'lastComputedDate': datetime.now().strftime('%Y-%m-%d'),
        'totalUserPrompts': len(all_prompts),
        'totalSessions': stats.get('totalSessions', 0),
        'totalMessages': stats.get('totalMessages', 0),
        'firstSessionDate': stats.get('firstSessionDate'),
        'modelUsage': stats.get('modelUsage', {}),
        'dailyActivity': daily_activity,
        'hourCounts': dict(prompts_by_hour),
        'longestSession': stats.get('longestSession')
    }

    # Write output
    output_path = Path(__file__).parent / 'claude_stats.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Parsed {len(all_prompts)} user prompts from {len(list(PROJECTS_DIR.iterdir()))} projects")
    print(f"Written to {output_path}")

if __name__ == '__main__':
    main()
