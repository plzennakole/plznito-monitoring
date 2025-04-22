import requests
import os
import json
import tqdm
import simplejson.errors
import logging
import argparse

logger = logging.getLogger(__name__)


def download_one_id(id):
    url = f"https://www.plznito.cz/api/1.0/tickets/detail/{id}"
    try:
        r = requests.get(url)
        out = r.json()
    except simplejson.errors.JSONDecodeError:
        print(f"Error with {id}")
        return {}
    return out


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
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)

    for i in tqdm.tqdm(range(1, 43568)):
        if not os.path.exists(os.path.join(args.data_dir, f"{i}.json")):
            json_data = download_one_id(i)
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
