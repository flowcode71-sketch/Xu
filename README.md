# XAUUSD Levels

Static, phone-first gold levels dashboard for GitHub Pages. Binance browser requests supply live price and candles; GitHub Actions calculates delayed GLD-options-derived levels. No paid server/API, chart library, CDN, or external script is needed.

## Set up GitHub Pages

1. Create a GitHub repository.
2. Add the provided files at the repository root; save the workflow at `.github/workflows/analyze.yml`.
3. Go to **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and save.
4. Go to **Settings → Pages**, choose **Deploy from a branch**, select the main branch and `/(root)`, then save.
5. In **Actions**, select **Analyze XAUUSD levels**, choose **Run workflow**, and run it manually once. The sample snapshot works before the first successful workflow run.
6. Open the Pages URL. The `#chart` URL fragment opens the Chart tab directly.

## Schedule and data

Weekday schedule: 00:30, 09:00, and 14:33 UTC, equivalent to 03:30, 12:00, and 17:33 Kenya time (EAT, UTC+3). The page calculates the next run using UTC math and skips weekends. It fetches `levels.json` every five minutes and displays the actual analysis time in Kenya time.

GitHub cron can run several minutes late; the page shows the real analysis time recorded by the workflow, not an assumed scheduled time. The workflow writes JSON atomically and only replaces `levels.json` after successful analysis. Failed runs leave the previous levels file untouched.

Options-derived values remain frozen between successful workflow runs. Binance price, distances, candles, and browser-derived levels refresh separately. The browser tries Binance XAUUSDT futures klines first and falls back to PAXGUSDT spot klines. The workflow uses the same spot sources and falls back to Yahoo gold futures.

GLD options data is delayed. Strikes are converted to XAUUSD with the ratio of XAUUSD to GLD price; estimates are informational only, not financial advice. Scheduled GitHub Actions may be delayed; use the manual workflow dispatch when needed. Alarm sound requires tapping **Arm alarms** and browser support; alarms only ring while this page is open and the screen is on. Keep the tab open. `data_prev.json` is committed by the workflow and stores the comparison baseline.

## Test checklist

- [ ] Countdown shows the next weekday run at 03:30, 12:00, or 17:33 Kenya time.
- [ ] Manual workflow run replaces sample data.
- [ ] A run while the US market is closed succeeds.
- [ ] Both tabs work.
- [ ] Chart pans and zooms.
- [ ] Level lines and labels line up.
- [ ] Each indicator toggles and persists.
- [ ] Alarm rings on a touch.
- [ ] Dismiss and Snooze work.
- [ ] A signal shows entry, SL, and TP.
- [ ] The stale banner does not fire over the weekend.
- [ ] The workflow has exactly these cron entries: `30 0 * * 1-5`, `0 9 * * 1-5`, and `33 14 * * 1-5`.
