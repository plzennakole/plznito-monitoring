import requests
import os
import json
import tqdm

def download_one_id(id):
    url = f"https://www.plznito.cz/api/1.0/tickets/detail/{id}"
    try:
        r = requests.get(url)
        out = r.json()
    except requests.exceptions.JSONDecodeError:
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

    os.makedirs("data", exist_ok=True)

    for i in tqdm.tqdm(range(1, 33874)):
        if not os.path.exists(f"data/{i}.json"):
            json_data = download_one_id(i)
            with open(f"data/{i}.json", "w") as f:
                json.dump(json_data, f)

    # load all data
    data = []
    for fname in tqdm.tqdm(os.listdir("data")):
        if fname.endswith(".json"):
            d = json.load(open(os.path.join("data", fname)))
            # get only "item"
            if "item" in d:
                d = d["item"]
            data.append(d)

    data_ = {"items": data}

    # save all data
    json.dump(data_, open("plznito_all.json", "w"), indent=4)

    # filter only cyklo
    data_cyklo = filter_data(data_)
    json.dump(data_cyklo, open("plznito_cyklo.json", "w"), indent=4)
