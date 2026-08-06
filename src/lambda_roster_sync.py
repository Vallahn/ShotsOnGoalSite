import hashlib
import json
import logging
from datetime import datetime
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
PLAYERS_TABLE = dynamodb.Table("NHL_Players")

TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", 
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", 
    "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS", "SEA", 
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"
]

def lambda_handler(event, context):
    logger.info("Starting weekly NHL Roster Sync...")
    added_count = 0
    updated_count = 0
    processed_teams = 0

    for team in TEAMS:
        url = f"https://api-web.nhle.com/v1/roster/{team}/current"
        try:
            res = requests.get(url, timeout=5)
            
            # If current roster fails or is empty, fall back to explicit season
            if res.status_code != 200 or not res.json():
                url = f"https://api-web.nhle.com/v1/roster/{team}/20252026"
                res = requests.get(url, timeout=5)

            if res.status_code != 200:
                logger.warning(f"Failed to fetch roster for {team}. Status: {res.status_code}")
                continue

            data = res.json()
            players = (
                data.get("forwards", [])
                + data.get("defensemen", [])
                + data.get("goalies", [])
            )

            if not players:
                logger.warning(f"No players found for team {team}")
                continue

            processed_teams += 1

            for p in players:
                p_id = int(p["id"])  # Ensure integer for DynamoDB 'N' attribute type
                first_name = p.get("firstName", {}).get("default", "")
                last_name = p.get("lastName", {}).get("default", "")
                position = p.get("positionCode", "")
                shoots_catches = p.get("shootsCatches", "")
                height = int(p.get("heightInInches", 0))
                weight = int(p.get("weightInPounds", 0))

                raw_attrs = f"{team}-{position}-{shoots_catches}-{height}-{weight}"
                curr_hash = hashlib.md5(raw_attrs.encode("utf-8")).hexdigest()

                # Fetch existing record from DynamoDB to compare
                response = PLAYERS_TABLE.get_item(Key={"player_id": p_id})
                existing_item = response.get("Item")

                is_new = not existing_item
                is_updated = existing_item and existing_item.get("data_hash") != curr_hash

                if is_new or is_updated:
                    PLAYERS_TABLE.put_item(
                        Item={
                            "player_id": p_id,
                            "first_name": first_name,
                            "last_name": last_name,
                            "team_tricode": team,
                            "position": position,
                            "shoots_catches": shoots_catches,
                            "height_inches": height,
                            "weight_lbs": weight,
                            "data_hash": curr_hash,
                            "last_updated": datetime.utcnow().isoformat(),
                        }
                    )

                    if is_new:
                        added_count += 1
                    else:
                        updated_count += 1

        except Exception as e:
            logger.error(f"Error processing team {team}: {str(e)}")

    summary = f"Roster Sync Complete. Teams Processed: {processed_teams}/{len(TEAMS)}. Added: {added_count}, Updated: {updated_count}"
    logger.info(summary)

    return {"statusCode": 200, "body": json.dumps(summary)}