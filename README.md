# Apple Careers Scraper

A Playwright-based scraper for [jobs.apple.com](https://jobs.apple.com) that filters results by role, location, years of experience, and posting date.

## Features

- Scrapes live job listings using headless Chrome
- Filters out senior/lead/manager titles automatically
- Filters out hardware department roles
- Filters by years of experience required (parsed from job descriptions)
- Filters by posting date (default: last 60 days)
- US-only results by default
- Deduplicates across runs and merges into a single JSON output

## Requirements

```bash
pip3 install requests playwright
python3 -m playwright install chromium
```

## Usage

```bash
# Basic search
python3 main.py --role "Software Engineer"

# With YoE cap
python3 main.py --role "ML Engineer" --max-yoe 3

# Last 30 days only
python3 main.py --role "Software Engineer" --max-yoe 3 --days 30

# Specific city
python3 main.py --role "Software Engineer" --location "Seattle"

# Run headless (no browser window)
python3 main.py --role "Software Engineer" --headless
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--role` | required | Job title to search |
| `--max-yoe` | none | Max years of experience required |
| `--location` | United States | Location filter |
| `--days` | 60 | Only jobs posted within N days |
| `--max` | 100 | Max jobs to return after filtering |
| `--output` | apple_jobs.json | Output file path |
| `--headless` | false | Hide the browser window |
| `--no-dedup` | false | Overwrite output instead of merging |

## Output

Results are saved to `apple_jobs.json`:

```json
{
  "meta": {
    "scraped_at": "2026-06-02T10:00:00Z",
    "filters": { "role": "ML Engineer", "max_yoe": 3, "location": "United States" },
    "total": 24
  },
  "jobs": [
    {
      "id": "200556722",
      "title": "ML Engineer, Apple Intelligence",
      "team": "Artificial Intelligence/Machine Learning",
      "location": "Seattle, Washington, United States",
      "posted_date": "2 weeks ago",
      "url": "https://jobs.apple.com/en-US/details/200556722",
      "min_yoe": 2,
      "hash": "abc123..."
    }
  ]
}
```
