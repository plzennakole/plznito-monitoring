import json
import os
import requests
from datetime import datetime
import logging
import bz2
import sys

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')


def get_plznito_current_data():
    """
    Download json with all plzni.to data
    """
    # http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=
    with requests.get(
            'http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=') as response:
        data = response.json()
        logging.info("Downloaded current plznito json.")
        return data
    return {}


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


def merge_data(data_old, data_new):
    """
    Merge two json, use the newer data
    """
    ids_new = [x["id"] for x in data_new]
    data_old = [x for x in data_old if x["id"] not in ids_new]
    data_out = data_old + data_new
    logging.info(f"Merging {len(data_old)} + {len(data_new)} to {len(data_out)} items.")
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


def db_update(json_db_file_path, out_dirname=""):
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
    # json.dump(data_current, open(fname, "w"), indent=4)
    logging.info(f"Saved to {fname}.")

    # filter only cyklo and convert to csv
    data_cyklo_current = filter_data(data_current)

    # add data to our db
    data_db = json.load(open(json_db_file_path))
    data_cyklo_updated = merge_data(data_db, data_cyklo_current)
    json.dump(data_cyklo_updated, open(json_db_file_path, "w"), indent=4)
    logging.info("Merging finished.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        db_update("plznito_cyklo.json", out_dirname="notebooks")
    elif len(sys.argv) == 2 and sys.argv[1] == "restore":
        db_restore(start_json_name="plznito_cyklo_04-10-2021.json", data_dirname="data")