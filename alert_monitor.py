name: Stock Alert Monitor

on:
  schedule:
    # Every 5 minutes Monday to Friday
    - cron: '*/5 * * * 1-5'
    # Daily summary 8:00am CT = 13:00 UTC (UTC-5 Central)
    - cron: '0 13 * * 1-5'
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install requests pytz

      - name: Run alert monitor
        env:
          SUPABASE_URL:       ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY:       ${{ secrets.SUPABASE_KEY }}
          FMP_KEY:            ${{ secrets.FMP_KEY }}
          GMAIL_ADDRESS:      ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: |
          timeout 240 python alert_monitor.py || true
