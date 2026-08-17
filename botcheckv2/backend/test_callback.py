import asyncio
from app import db
import json

camp = db.get_campaign(10) # Assuming ID might be 10, let's just get the latest giveaway
camps = [c for c in db.get_campaigns() if c["type"] == "giveaway"]
if not camps:
    print("No giveaway campaign found")
else:
    camp = camps[-1]
    print("Campaign:", camp)
    try: config = json.loads(camp.get("config") or "{}")
    except Exception as e: 
        print("Config error:", e)
        config = {}
    
    print("Config:", config)
    try:
        min_reward = int(config.get("min_reward") or 1000)
        max_reward = int(config.get("max_reward") or 5000)
        print("Min:", min_reward, "Max:", max_reward)
    except Exception as e:
        print("Reward parsing error:", e)

