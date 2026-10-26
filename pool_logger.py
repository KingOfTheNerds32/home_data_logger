import iaqualink
import asyncio
import pandas as pd
from datetime import datetime
import os
import pytz
import httpx
from iaqualink.client import AqualinkClient

async def harvest_pool_data(ts_string):
    USERNAME = os.environ.get('IQUALINK_USER')
    PASSWORD = os.environ.get('IQUALINK_PASS')

    async with AqualinkClient(USERNAME, PASSWORD) as client:
        # 1. SET TIMEOUT HERE
        # The internal _client is now initialized, so we can override its timeout.
        # This gives Jandy's servers 60s to respond to prevent ReadTimeout.
        client._client.timeout = httpx.Timeout(60.0, connect=60.0)

        try:
            # 2. LOGIN AND FETCH
            systems = await client.get_systems()
        except Exception as e:
            print(f"Primary login failed: {e}. Retrying in 10s...")
            await asyncio.sleep(10)
            systems = await client.get_systems()
            
        if not systems:
            raise RuntimeError("No iAquaLink systems found on account")

        system = list(systems.values())[0]
        await system.update()
        
        row_data = {dev_id: device.state for dev_id, device in system.devices.items()}

        # Helper to convert 0/1 or "0"/"1" to ON/OFF
        def on_off(val):
            if val is None or val == "": return "OFF"
            return "ON" if str(val) == "1" else "OFF"

        final_row = {
            'timestamp': ts_string, #shared timestamp passed in
            
            # Temperatures
            'air_temp': row_data.get('air_temp', 'N/A'),
            'pool_temp': row_data.get('pool_temp', 'N/A'),
            'spa_temp': row_data.get('spa_temp', 'N/A'),
            
            # Set Points
            'pool_set_point': row_data.get('pool_set_point', 'N/A'),
            'spa_set_point': row_data.get('spa_set_point', 'N/A'),
            
            # Equipment Status
            'pool_pump': on_off(row_data.get('pool_pump')),
            'spa_pump': on_off(row_data.get('spa_pump')),
            'pool_heater': on_off(row_data.get('pool_heater')),
            'spa_heater': on_off(row_data.get('spa_heater')),
            'solar_heater': on_off(row_data.get('solar_heater')),
            'freeze_protection': on_off(row_data.get('freeze_protection')),
            
            # Lights & Features
            'pool_light': on_off(row_data.get('aux_1')),
            'spa_light': on_off(row_data.get('aux_2')),
            'spillover': on_off(row_data.get('aux_3')),
            'jet_pump': on_off(row_data.get('aux_4')),
            
            # System Info & Chemistry
            'relay_count': row_data.get('relay_count', 'N/A'),
            'chlorinator_present': row_data.get('is_icl_present', 'N/A'),
            'ph': row_data.get('ph', 'N/A'),
            'orp': row_data.get('orp', 'N/A'),
            'salinity': row_data.get('pool_salinity', 'N/A')
        }

        # Save to CSV
        df = pd.DataFrame([final_row])
        file_path = 'pool_history.csv'
        header_needed = not os.path.exists(file_path) or os.stat(file_path).st_size == 0
        df.to_csv(file_path, mode='a', header=header_needed, index=False)
        print(f"Logged all equipment states at {ts_string} PT")

if __name__ == "__main__":
    pacific_tz = pytz.timezone('America/Los_Angeles')
    asyncio.run(harvest_pool_data(datetime.now(pacific_tz).strftime("%Y-%m-%d %H:%M:%S")))
