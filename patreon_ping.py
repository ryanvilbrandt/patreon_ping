import os
from collections import defaultdict
from configparser import RawConfigParser
from datetime import datetime
from json import load, dump
from json.decoder import JSONDecodeError
from time import sleep, ctime

import requests
from dateutil.relativedelta import relativedelta
from discord_webhook import DiscordWebhook

DEBUG = False

config = RawConfigParser()
config.read("config.ini")
PATREON_AUTH_TOKEN = config.get("Tokens", "Patreon")
PUBLIC_DISCORD_ENDPOINT = config.get("Discord Endpoints", "Public")
PRIVATE_DISCORD_ENDPOINT = config.get("Discord Endpoints", "Private")


def call_patreon(cache_reponse=False):
    filepath = "cached_patrons.json"
    if cache_reponse:
        if os.path.exists(filepath):
            with open(filepath) as f:
                d = load(f)
                return d["patrons"], d["two_fifty_tier_id"]

    print("Retrieving data from Patreon...")
    url = "https://www.patreon.com/api/oauth2/v2/campaigns/82133/members?include=currently_entitled_tiers&fields[member]=full_name,lifetime_support_cents,patron_status,pledge_relationship_start,currently_entitled_amount_cents&fields[tier]=amount_cents"
    # "// Sample response for https://www.patreon.com/api/oauth2/v2/campaigns/{campaign_id}/members?include=currently_entitled_tiers,address&fields[member]=full_name,is_follower,last_charge_date,last_charge_status,lifetime_support_cents,currently_entitled_amount_cents,patron_status&fields[tier]=amount_cents,created_at,description,discord_role_ids,edited_at,patron_count,published,published_at,requires_shipping,title,url"
    headers = {"Authorization": PATREON_AUTH_TOKEN}
    patrons = []
    two_fifty_tier_id = None

    while True:
        response = requests.request("GET", url, headers=headers)
        try:
            json_response = response.json()
        except JSONDecodeError:
            print(response)
            print(response.content)
            raise
        patrons += json_response["data"]
        print(len(patrons))
        if two_fifty_tier_id is None:
            for i in json_response["included"]:
                if i["type"] == "tier" and i["attributes"].get("amount_cents") == 25000:
                    two_fifty_tier_id = i["id"]
                    print(f"$250 tier ID: {two_fifty_tier_id}")
                    break
        if not "links" in json_response:
            break
        url = json_response["links"]["next"]

    if cache_reponse:
        with open(filepath, "w") as f:
            d = {"patrons": patrons, "two_fifty_tier_id": two_fifty_tier_id}
            dump(d, f)

    return patrons, two_fifty_tier_id


def get_patrons_db():
    if not os.path.exists("patrons_db.json"):
        return {"$1000+": [], "Year lists": {}, "$250 tier": []}
    with open("patrons_db.json") as f:
        return load(f)


def check_patrons(patrons_db, debug):
    print(f"\n{ctime()}\nCalling Patreon...")
    patrons_list, two_fifty_tier_id = call_patreon(cache_reponse=debug)
    print("Checking for updates...")
    check_1000_patrons(patrons_list, patrons_db)
    check_patron_anniversaries(patrons_list, patrons_db)
    check_250_tier_patrons(patrons_list, patrons_db, two_fifty_tier_id)
    print("Done.")


def check_1000_patrons(patrons_list, patrons_db):
    """
    Get patrons who have pledged over $1000 over their lifetime
    Compare against DB list
    If new patrons, update DB and call Discord webhook
    """
    thousand_list = patrons_db["$1000+"]
    new_thousand_list = []
    for p in patrons_list:
        attr = p["attributes"]
        full_name = attr["full_name"]
        if attr["lifetime_support_cents"] > 100000:
            new_thousand_list.append(full_name)
            if full_name not in thousand_list:
                call_discord_webhook("Private", f"{full_name} has pledged over $1000 in their lifetime!")
    if new_thousand_list != thousand_list:
        patrons_db["$1000+"] = new_thousand_list
        update_patrons_db(patrons_db)


def check_patron_anniversaries(patrons_list, patrons_db):
    """
    Filter patrons by year
    Compare against DB list
    If lists changed, update DB and call Discord webhook
    """
    year_lists = patrons_db["Year lists"]
    new_year_lists = defaultdict(list)
    for p in patrons_list:
        attr = p["attributes"]
        if attr["patron_status"] != "active_patron":
            continue
        full_name = attr["full_name"]
        dt = datetime.fromisoformat(attr["pledge_relationship_start"])
        now_dt = datetime.now(dt.tzinfo)
        difference_in_years = relativedelta(now_dt, dt).years
        if difference_in_years > 0:
            difference_in_years = str(difference_in_years)
            new_year_lists[difference_in_years].append(full_name)
            if full_name not in year_lists.get(difference_in_years, []):
                call_discord_webhook("Private", f"{full_name} has had their {difference_in_years} year anniversary!")
    if new_year_lists != year_lists:
        patrons_db["Year lists"] = new_year_lists
        update_patrons_db(patrons_db)


def check_250_tier_patrons(patrons_list, patrons_db, two_fifty_tier_id):
    """
    Get patrons currently pledging $250
    Compare against DB list
    If any patrons have unpledged, update DB and call Discord webhook
    """
    two_fifty_list = patrons_db["$250 tier"]
    new_two_fifty_list = []
    for p in patrons_list:
        attr = p["attributes"]
        if attr["patron_status"] != "active_patron":
            continue
        full_name = attr["full_name"]
        for tier in p["relationships"]["currently_entitled_tiers"]["data"]:
            if tier["id"] == two_fifty_tier_id:
                new_two_fifty_list.append(full_name)
    if new_two_fifty_list != two_fifty_list:
        if len(new_two_fifty_list) < 4:
            call_discord_webhook("Public", f"There is a free slot in the $250 tier! Grab it quick!")
        patrons_db["$250 tier"] = new_two_fifty_list
        update_patrons_db(patrons_db)


def update_patrons_db(patrons_db):
    with open("patrons_db.json", "w") as f:
        dump(patrons_db, f, indent=4)


def call_discord_webhook(endpoint, content):
    print(content)
    if DEBUG:
        return
    if endpoint == "Public":
        url = PUBLIC_DISCORD_ENDPOINT
    elif endpoint == "Private":
        url = PRIVATE_DISCORD_ENDPOINT
    else:
        raise ValueError(f"THE FUCK YOU SMOKING, SON?? {endpoint} ISN'T A GODDAMN DISCORD ENDPOINT!")
    webhook = DiscordWebhook(url=url, content=content)
    print(webhook.execute())
    sleep(1)  # Avoid rate limiting


def main(debug=False):
    patrons_db = get_patrons_db()
    try:
        while True:
            check_patrons(patrons_db, debug)
            sleep(10 * 60)  # Every ten minutes
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(DEBUG)
