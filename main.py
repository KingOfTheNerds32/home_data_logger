import asyncio
import os
import pandas as pd
from datetime import datetime
import pytz

# Import the functions from your other files
from pool_logger import harvest_pool_data
from weather_logger import harvest_weather_data

# Alert after this many consecutive failed runs (~15 min at a 5 min cadence),
# then re-alert every ALERT_REPEAT failed runs (~6 hours) while still down.
ALERT_THRESHOLD = 3
ALERT_REPEAT = 72


def consecutive_failures(df_status, column):
    """Count how many of the most recent runs failed for a service."""
    count = 0
    for status in reversed(df_status[column].tolist()):
        if status == 'OK':
            break
        count += 1
    return count

async def main():
    # 1. SETUP TIMEZONES AND CURRENT TIME
    # GitHub Runners use UTC by default, so we must be explicit about Pacific time
    pacific_tz = pytz.timezone('America/Los_Angeles')
    now = datetime.now(pacific_tz)
    unified_ts = now.strftime("%Y-%m-%d %H:%M:%S")
    
    results = {"Pool": False, "Weather": False}
    errors = {"Pool": "", "Weather": ""}
    
    print(f"Starting telemetry collection for: {unified_ts}")

    # 2. CALCULATE TIME SINCE LAST SUCCESSFUL POOL SYNC
    last_sync_text = "No previous successful sync found"
    status_file = 'execution_log.csv'
    
    if os.path.exists(status_file):
        try:
            df_status = pd.read_csv(status_file)
            # Filter for rows where pool was OK and timestamp is not null
            successes = df_status[(df_status['pool_status'] == 'OK') & (df_status['timestamp'].notnull())]
            
            if not successes.empty:
                # Get the most recent success timestamp
                last_ok_str = str(successes['timestamp'].iloc[-1])
                
                # Convert string to naive datetime, then localize to PST
                naive_dt = datetime.strptime(last_ok_str, "%Y-%m-%d %H:%M:%S")
                last_ok_dt = pacific_tz.localize(naive_dt)
                
                # Calculate difference
                diff = now - last_ok_dt
                total_seconds = int(diff.total_seconds())
                
                if total_seconds < 60:
                    last_sync_text = "Just now"
                else:
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    
                    parts = []
                    if hours > 0: parts.append(f"{hours}h")
                    if minutes > 0: parts.append(f"{minutes}m")
                    last_sync_text = f"{' '.join(parts)} ago"
        except Exception as e:
            print(f"Metadata lookup failed: {e}")
            last_sync_text = "Error calculating last sync"

    # 3. RUN POOL LOGGER (WITH 3 RETRIES)
    # This addresses the intermittent iAquaLink ReadTimeout issues
    for attempt in range(1, 4):
        try:
            print(f"Pool Sync Attempt {attempt}/3...")
            await harvest_pool_data(unified_ts)
            results["Pool"] = True
            errors["Pool"] = "Synchronized"
            print("Successfully logged pool data.")
            break 
        except Exception as e:
            errors["Pool"] = str(e)
            if attempt < 3:
                print(f"Attempt {attempt} failed: {e}. Retrying in 10s...")
                await asyncio.sleep(10)
            else:
                print(f"All 3 pool attempts failed. Final error: {e}")

    # 4. RUN WEATHER LOGGER (WITH 3 RETRIES)
    for attempt in range(1, 4):
        try:
            print(f"Weather Sync Attempt {attempt}/3...")
            harvest_weather_data(unified_ts)
            results["Weather"] = True
            print("Successfully logged weather data.")
            break
        except Exception as e:
            errors["Weather"] = str(e)
            if attempt < 3:
                print(f"Attempt {attempt} failed: {e}. Retrying in 10s...")
                await asyncio.sleep(10)
            else:
                print(f"All 3 weather attempts failed. Final error: {e}")

    # 5. LOG TO HISTORICAL EXECUTION CSV
    status_row = {
        'timestamp': unified_ts,
        'pool_status': 'OK' if results["Pool"] else 'FAIL',
        'weather_status': 'OK' if results["Weather"] else 'FAIL'
    }
    pd.DataFrame([status_row]).to_csv(status_file, mode='a', 
                                     header=not os.path.exists(status_file), index=False)

    # 6. DECIDE WHETHER TO ALERT
    # Single failures are normal (iAquaLink is flaky) and heal on the next run,
    # so only alert once a service has been down for several runs in a row.
    alerts = []
    try:
        df_status = pd.read_csv(status_file)
        for service, column in (("Pool", 'pool_status'), ("Weather", 'weather_status')):
            streak = consecutive_failures(df_status, column)
            if streak >= ALERT_THRESHOLD and (streak - ALERT_THRESHOLD) % ALERT_REPEAT == 0:
                alerts.append(f"{service} has failed {streak} runs in a row. Last error: {errors[service]}")
    except Exception as e:
        print(f"Alert check failed: {e}")

    for alert in alerts:
        print(f"ALERT: {alert}")
    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a') as f:
            f.write(f"alert={'true' if alerts else 'false'}\n")

    # 7. WRITE GITHUB STEP SUMMARY (MARKDOWN TABLE)
    with open("job_summary.md", "w") as f:
        f.write("## 🚀 Telemetry Run Summary\n")
        f.write(f"**Current Run:** `{unified_ts}`\n")
        f.write(f"**Last Successful Pool Sync:** `{last_sync_text}`\n\n")
        f.write("| Service | Status | Details |\n")
        f.write("| :--- | :--- | :--- |\n")
        
        pool_icon = '✅ OK' if results["Pool"] else '❌ FAILED'
        f.write(f"| Pool Data | {pool_icon} | {errors['Pool']} |\n")
        
        weather_icon = '✅ OK' if results["Weather"] else '❌ FAILED'
        weather_details = "Synchronized" if results["Weather"] else errors["Weather"]
        f.write(f"| Weather Data | {weather_icon} | {weather_details} |\n")

        if alerts:
            f.write("\n### ⚠️ Alerts\n")
            for alert in alerts:
                f.write(f"- {alert}\n")

if __name__ == "__main__":
    asyncio.run(main())
