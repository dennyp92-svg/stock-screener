name: Stock Alert Monitor

on:
  schedule:
    # Alert monitor every 5 minutes Monday to Friday
    - cron: '*/5 * * * 1-5'
    # Morning brief once at 8:00am CT = 13:00 UTC
    - cron: '0 13 * * 1-5'
  workflow_dispatch:

jobs:
  morning_brief:
    runs-on: ubuntu-latest
    if: github.event.schedule == '0 13 * * 1-5'

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install requests pytz supabase python-dotenv

      - name: Send morning brief
        env:
          SUPABASE_URL:       ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY:       ${{ secrets.SUPABASE_KEY }}
          FMP_KEY:            ${{ secrets.FMP_KEY }}
          GMAIL_ADDRESS:      ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: |
          timeout 120 python -c "
          import os, sys
          sys.path.insert(0, '.')
          from alert_monitor import send_daily_summary
          send_daily_summary()
          print('Morning brief done')
          "

  monitor:
    runs-on: ubuntu-latest
    if: github.event.schedule == '*/5 * * * 1-5' || github.event_name == 'workflow_dispatch'

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install requests pytz supabase python-dotenv

      - name: Run alert monitor
        env:
          SUPABASE_URL:       ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY:       ${{ secrets.SUPABASE_KEY }}
          FMP_KEY:            ${{ secrets.FMP_KEY }}
          GMAIL_ADDRESS:      ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          SEND_BRIEF:         "false"
        run: |
          timeout 240 python alert_monitor.py || true
