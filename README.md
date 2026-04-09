# Work Audit System

AI-powered freelancer work audit system. Analyzes HiveDesk timesheet exports and screenshot reports to verify work activity patterns.

## Features

- **Timesheet analysis** — session durations, activity rates, anomaly detection
- **Screenshot analysis** — AI vision classifies each screenshot (work / non-work / idle)
- **Cross-analysis** — compares both sources for contradictions or consistencies
- **Work summary** — plain-language ~100 word report describing the freelancer's work
- **Risk scoring** — 0-100 score with detailed reasoning

## Project Structure

```
├── app.py                  # Streamlit web UI
├── main.py                 # CLI entry point
├── orchestrator/
│   └── workflow.py          # LangGraph workflow (6 nodes)
├── parsers/
│   ├── sql_parser.py        # Timesheet parser (.xls, .csv)
│   └── screenshot_parser.py # PDF screenshot parser
├── analysis/
│   ├── timesheet_analysis.py
│   ├── screenshot_analysis.py
│   └── prompts.py           # All LLM prompt templates
├── fusion/
│   └── evidence_fusion.py   # Cross-analysis & risk scoring
├── schemas/
│   └── models.py            # All Pydantic data models
├── utils/
│   ├── config.py            # Settings (from env vars)
│   └── helpers.py           # Utility functions
├── requirements.txt
├── Procfile                 # Render start command
├── runtime.txt              # Python version for Render
├── .streamlit/
│   └── config.toml          # Streamlit configuration
└── .env.example             # Environment variable template
```

## Local Development

```bash
# Clone the repo
git clone <your-repo-url>
cd work-audit-system

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env and add your OpenAI API key

# Run the web UI
streamlit run app.py

# Or use the CLI
python main.py --timesheet data/timesheet.xls --screenshots data/report.pdf
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for GPT-4o |
| `LLM_MODEL` | No | `gpt-4o` | Model to use for LLM calls |
| `MAX_SCREENSHOTS` | No | `0` | Max screenshots to analyze (0 = all) |
| `LOG_LEVEL` | No | `INFO` | Logging level |

---

## Deploy to Render

### Step 1 — Push to GitHub

```bash
cd "path/to/your/project"
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/work-audit-system.git
git push -u origin main
```

### Step 2 — Create a Render Web Service

1. Go to [https://dashboard.render.com](https://dashboard.render.com)
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub account if not already connected
4. Select your **work-audit-system** repository
5. Configure the service:

| Setting | Value |
|---------|-------|
| **Name** | `work-audit-system` (or any name you want) |
| **Region** | Choose the closest to you |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false` |
| **Instance Type** | `Starter` or higher (needs enough RAM for pandas + duckdb) |

### Step 3 — Set Environment Variables

In the Render dashboard, go to your service → **"Environment"** tab → **"Add Environment Variable"**:

| Key | Value |
|-----|-------|
| `OPENAI_API_KEY` | `sk-your-actual-api-key` |
| `LLM_MODEL` | `gpt-4o` |
| `MAX_SCREENSHOTS` | `10` |
| `LOG_LEVEL` | `INFO` |
| `PYTHON_VERSION` | `3.12.8` |

### Step 4 — Deploy

1. Click **"Create Web Service"**
2. Render will install dependencies and start the app
3. Wait for the build to finish (usually 2-5 minutes)
4. Your app will be live at `https://work-audit-system.onrender.com`

### Troubleshooting

- **Build fails**: Check the build logs in Render dashboard. Most common issue is a missing dependency — make sure `requirements.txt` is complete.
- **App crashes on start**: Check that `OPENAI_API_KEY` is set in environment variables. The app will still start without it but LLM features will fail.
- **Upload size limit**: The `.streamlit/config.toml` sets max upload to 50MB. Render free tier has limited disk — keep uploads reasonable.
- **Slow cold starts**: Render free tier spins down after inactivity. First request may take 30-60 seconds. Upgrade to a paid plan for always-on.
