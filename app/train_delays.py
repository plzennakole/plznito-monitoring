import requests
from bs4 import BeautifulSoup
from fake_headers import Headers
from flask import Flask, jsonify
from flask_caching import Cache

#app = Flask(__name__)


def get_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text()


def get_delay(text):
    if not text:
        return None
    if "bez zpoždění" in text:
        return 0
    if "včas" in text:
        return 0
    if "zrušen" in text:
        return None
    if "odklon" in text:
        return None
    if "výluka" in text:
        return None
    return int(text.split()[0])


def scrape_babitron_delays(url):
    results = {}

    # generate headers
    headers = Headers(headers=True).generate()

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Chyba při stahování stránky: {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")

    tables = soup.find_all("table", {"align": "CENTER", "bgcolor": "0000ff"})

    if len(tables) == 0:
        print("Tabulka se zpožděními nebyla nalezena.")
        return results

    for table in tables:

        rows = table.find_all("tr")
        for row in rows[1:]:
            #cells = row.find_all("td")
            s_row = str(row)
            s_row = s_row.replace("</td>", "")
            s_row = s_row.replace("</tr>", "")
            s_row = s_row.replace("</a>", "")

            columns = s_row.split("<td>")

            train_info = get_text(columns[1])
            train_name = columns[2]
            route = columns[3]
            station = columns[4]
            scheduled_actual_time = columns[5]
            delay_info = get_delay(columns[6])

            results[train_info] = {
                    "train": train_info,
                    "name": train_name,
                    "route": route,
                    "station": station,
                    "scheduled_actual_time": scheduled_actual_time,
                    "delay_text": columns[6],
                    "delay": delay_info
            }

    return results

# @app.route('/train_delays', methods=['GET'])
# @cache.cached(timeout=60)
# def get_delays():
#     delays_r = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponline")
#     delays_os = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponlineos")
#     delays = {**delays_r, **delays_os}
#     return jsonify(delays)
#
#
# if __name__ == "__main__":
#     app.run(debug=True)