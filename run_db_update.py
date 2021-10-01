import json
import os
import requests
from datetime import datetime
import logging


logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')


def get_plznito_current_data():
    """
    Download json with all plzni.to data
    """
    # http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=
    with requests.get('http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term=') as response:
        data = response.json()
        logging.info("Downloaded current plznito json.")
        return data
    return {}


def filter_data(data):
    """
    Simple filtering of cycling items
    """
    data_cyklo = [x for x in data["items"] if "cykl" in x["report"].lower() or "kolob" in x["report"].lower() or "cikli" in x["report"].lower()]
    logging.info(f"Filtered {len(data_cyklo)} cycling items.")
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


def db_restore():
    # how to process all data
    # aka DB restore
    data = {[]}
    for i in sorted(os.listdir(".")):
        if i.endswith(".json"):
            data_new = json.load(open(i))
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
    fname = os.path.join(out_dirname, datetime.today().strftime('%Y-%m-%d-%H:%M:%S')+".json")
    json.dump(data_current, open(fname, "w"), indent=4)
    logging.info(f"Saved to {fname}.")

    # filter only cyklo and convert to csv
    data_cyklo_current = filter_data(data_current)

    # add data to our db
    data_db = json.load(open(json_db_file_path))
    data_cyklo_updated = merge_data(data_db, data_cyklo_current)
    json.dump(data_cyklo_updated, open(json_db_file_path, "w"), indent=4)
    logging.info("Merging finished.")


if __name__ == "__main__":
    db_update("plznito_cyklo.json", out_dirname="notebooks")
