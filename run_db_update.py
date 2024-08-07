import argparse
import bz2
import json
import logging
import os
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def get_plznito_current_data():
    """
    Download json with all plzni.to data
    """
    # http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=
    with requests.get(
            'http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=') as response:
        data = response.json()
        logger.info("Downloaded current plznito json.")
        return data


def filter_data(data):
    """
    Simple filtering of cycling items
    """
    data_cyklo = []
    for x in data["items"]:
        if not "report" in x:
            print(f"report not found in data {x}")
            continue
        if "cykl" in x["report"].lower() or "kolob" in x["report"].lower() or "cikli" in x["report"].lower() \
                or "cyklo" in x["name"].lower() or "kolob" in x["name"].lower():
            if "recykl" not in x["report"].lower():
                data_cyklo.append(x)
    return data_cyklo


def merge_data(data_old, data_new):
    """
    Merge two json, use the newer data
    """
    ids_new = [x["id"] for x in data_new]
    data_old = [x for x in data_old if x["id"] not in ids_new]
    data_out = data_old + data_new
    logger.info(f"Merging {len(data_old)} + {len(data_new)} to {len(data_out)} items.")
    return data_out


def db_restore(start_json_name=None, data_dirname="."):
    # how to process all data
    # aka DB restore
    if start_json_name is not None:
        data = json.load(open(start_json_name))
    else:
        data = []

    for fname in sorted(os.listdir(data_dirname)):
        full_fname = os.path.join(data_dirname, fname)
        if full_fname.endswith(".json"):
            data_new = json.load(open(full_fname))
            data_cyklo = filter_data(data_new)
            data = merge_data(data, data_cyklo)
        elif full_fname.endswith("bz2"):
            data_new = json.loads(bz2.decompress(open(full_fname, "rb").read()))
            data_cyklo = filter_data(data_new)
            data = merge_data(data, data_cyklo)

    json.dump(data, open("plznito_cyklo.json", "w"), indent=4)


def db_update(json_db_file_path, out_dirname="", filter_cyklo=True):
    """
    update with daily data
    """
    # load new data
    data_current = get_plznito_current_data()

    # save all for later
    fname = os.path.join(out_dirname, datetime.today().strftime('%Y-%m-%d-%H:%M:%S') + ".json")
    # dump to bz2
    with bz2.open(fname + ".bz2", "wt") as f:
        json.dump(data_current, f, indent=4)
    logging.info(f"Saved to {fname}.")

    # add data to our db
    data_db = json.load(open(json_db_file_path))

    if filter_cyklo:
        # filter only cyklo and convert to csv
        data_cyklo_current = filter_data(data_current)
        print(len(data_cyklo_current))
        data_cyklo_updated = merge_data(data_db, data_cyklo_current)
        json.dump(data_cyklo_updated, open(json_db_file_path, "w"), indent=4)
    else:
        print(data_current["items"])
        data_updated = merge_data(data_db, [x for x in data_current["items"]])
        json.dump(data_updated, open(json_db_file_path, "w"), indent=4)

    logging.info("Merging finished.")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--db_json", type=str, default="plznito_cyklo.json")
    parser.add_argument("--filter_cyklo", action="store_true")
    parser.add_argument("--restore", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(filename='plznito_monitoring.log',
                        level=logging.INFO,
                        format='%(asctime)s %(message)s')

    logger.info(f"Running with args: {args}")

    db_update(args.db_json, out_dirname="notebooks", filter_cyklo=args.filter_cyklo)
    if args.restore:
        db_restore(start_json_name="plznito_cyklo_04-10-2021.json", data_dirname="data")
