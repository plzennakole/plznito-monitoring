import requests
import os
import json
import tqdm
import simplejson.errors
import logging
import argparse
import re
import json5
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)


def download_one_id(id, source="auto"):
    url = f"https://www.plznito.cz/api/1.0/tickets/detail/{id}"
    if source in ("auto", "api"):
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            out = r.json()
            if out:
                return out
        except (simplejson.errors.JSONDecodeError, RequestException, ValueError) as e:
            logger.warning("API failed for id %s: %s", id, e)
            if source == "api":
                return {}
    return scrape_one_id_from_map(id)


def scrape_one_id_from_map(id):
    url = f"https://www.plznito.cz/map/{id}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except RequestException as e:
        logger.warning("Map fetch failed for id %s: %s", id, e)
        return {}

    try:
        locations = _parse_locations_from_html(r.text)
    except Exception as e:
        logger.warning("Failed to parse locations for id %s: %s", id, e)
        return {}

    for location in locations:
        if str(location.get("id")) == str(id):
            return {"item": _location_to_item(location)}

    logger.warning("No matching item for id %s in map page", id)
    return {}


def _parse_locations_from_html(html):
    match = re.search(r"var\s+locations\s*=\s*(\[[\s\S]*?\])\s*;", html)
    if not match:
        return []
    locations_js = match.group(1)
    return json5.loads(locations_js)


def _location_to_item(location):
    # Map the lightweight map payload to the fields used by the rest of the script.
    return {
        "id": location.get("id"),
        "name": location.get("name", ""),
        "report": location.get("description", ""),
        "description": location.get("description", ""),
        "solution": location.get("solution"),
        "latitude": str(location.get("lat", "")) if "lat" in location else "",
        "longitude": str(location.get("lng", "")) if "lng" in location else "",
        "status": location.get("status"),
        "status_id": location.get("status_id"),
        "category": location.get("category"),
        "address": location.get("address", ""),
        "photos": location.get("photos", []),
        "date": location.get("date"),
    }


def filter_data(data):
    """
    Simple filtering of cycling items
    """
    data_cyklo = []
    for x in data["items"]:
        if not "report" in x:
            print(f"report not found in data {x}")
            continue
        if "cykl" in x["report"].lower() or "kolob" in x["report"].lower() or "cikli" in x["report"].lower()\
                or "cyklo" in x["name"].lower() or "kolob" in x["name"].lower():
            if "recykl" not in x["report"].lower():
                data_cyklo.append(x)
    return data_cyklo


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", type=str, default="data")
    parser.add_argument("--source", type=str, default="auto", choices=["auto", "api", "web"],
                        help="Fetch source: auto (API fallback to web), api, or web.")
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)

    for i in tqdm.tqdm(range(42260, 49554)):
        # delete a local file if it exists but is empty or invalid
        if os.path.exists(os.path.join(args.data_dir, f"{i}.json")):
            try:
                data = json.load(open(os.path.join(args.data_dir, f"{i}.json")))
            except JSONDecodeError:
                data = {}
            if data == {}:
                os.remove(os.path.join(args.data_dir, f"{i}.json"))
        # download data
        if not os.path.exists(os.path.join(args.data_dir, f"{i}.json")):
            if args.source == "web":
                json_data = scrape_one_id_from_map(i)
            else:
                json_data = download_one_id(i, source=args.source)
            with open(os.path.join(args.data_dir, f"{i}.json"), "w") as f:
                json.dump(json_data, f)

    # load all data
    data = []
    for fname in tqdm.tqdm(os.listdir(args.data_dir)):
        if fname.endswith(".json"):
            d = json.load(open(os.path.join(args.data_dir, fname)))
            # get only "item"
            if "item" in d:
                d = d["item"]
            data.append(d)

    # save all data, get just the items
    # filet out emtpy items
    data = [x for x in data if x != {}]
    json.dump(data, open("plznito_all.json", "w"), indent=4)

    # filter only cyklo
    data_ = {"items": data}
    data_cyklo = filter_data(data_)
    json.dump(data_cyklo, open("plznito_cyklo.json", "w"), indent=4)
