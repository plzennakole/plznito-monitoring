import folium
from folium.plugins import MarkerCluster, Fullscreen
import json
import os
import datetime
import logging
import argparse

logger = logging.getLogger(__name__)



try:
    ipynb_path
except NameError:
    ipynb_path = os.getcwd()


def get_map(data_current, cluster=False):
    m = folium.Map(location=[49.7443392, 13.3766164], zoom_start=13, control_scale=True)

    # make groups for the years
    years = {}
    this_year = datetime.datetime.now().year
    this_day = datetime.datetime.now()
    for y in range(2014, this_year + 1):
        years[y] = folium.FeatureGroup(name=str(y), show=False)
        if cluster:
            years[y] = MarkerCluster(name=str(y)).add_to(years[y])
    years["last_30_days"] = folium.FeatureGroup(name="Posledních 30 dnů", show=True)
    if cluster:
        years["last_30_days"] = MarkerCluster(name="Posledních 30 dnů").add_to(years["last_30_days"])

    for item in data_current:
        if item['status_id'] == 2:
            color = "orange"
        elif item['status_id'] == 3:
            color = "green"
        elif item['status_id'] == 6:
            color = "lightgreen"
        else:
            color = "red"

        date = item['created']['date']
        # get date for processing
        try:
            date_time_obj = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            date_time_obj = datetime.datetime.strptime(date, '%Y-%m-%d %H:%M:%S')

        description = item['description'].replace('\n', '<br>')
        solution = str(item['solution']).replace('\n', '<br>')

        text = f"<b>{item['name']} " \
               f"(<a href='http://plzni.to/map#!/activity/{item['id']}' target='_blank'>{item['id']}</a>)</b><br>" \
               f"{date}<br>{description}<br><br>{solution}<br>"

        if len(item['photos']) > 0:
            text += f"<img src='{item['photos'][0]['thumb'].replace('https', 'http')}'>"

        popup = folium.Popup(text, max_width=300, min_width=300)
        if item["latitude"] is None or item["longitude"] is None:
            logger.debug(f"No LAT and LON in {item}")
            continue
        marker = folium.Marker(
            location=[item["latitude"], item["longitude"]],
            popup=popup,
            icon=folium.Icon(color=color, icon="ok-sign"),
        )

        # put to "last 30 days" or correspondent year
        if this_day - datetime.timedelta(days=30) < date_time_obj:
            marker.add_to(years["last_30_days"])
        else:
            marker.add_to(years[date_time_obj.year])

    for k, v in years.items():
        v.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    legend_html = '''
         <div style="position: fixed; 
         top: 50px; left: 50px; width: 140px; height: 160px; 
         border:2px solid grey; z-index:9999; font-size:14px;
         ">&nbsp; Legenda <br>
         &nbsp; Vyřešeno &nbsp; <i class="fa fa-map-marker fa-2x"
                      style="color:green"></i><br>
         &nbsp; Odpovězeno &nbsp; <i class="fa fa-map-marker fa-2x"
                      style="color:lightgreen"></i><br>
         &nbsp; V řešení &nbsp; <i class="fa fa-map-marker fa-2x"
                      style="color:orange"></i><br>
         &nbsp; Odmítnuto / nevyřešeno  &nbsp; <i class="fa fa-map-marker fa-2x"
                      style="color:red"></i>
          </div>
         '''
    m.get_root().html.add_child(folium.Element(legend_html))

    # plus_button_html = '''<a href="#" class="w3-button w3-large w3-circle w3-green w3-ripple" style="position: fixed;
    #     top: 50px; left: 50px; z-index:9999;">+</a>'''
    # m.get_root().html.add_child(folium.Element(plus_button_html))
    # plus overlay form to get new points to map
    # https://morioh.com/p/f23f87a146b4

    Fullscreen(position='topright',  # ‘topleft’, default=‘topright’, ‘bottomleft’, ‘bottomright’
               title='FULL SCREEN ON',
               title_cancel='FULL SCREEN OFF',
               force_separate_button=True
               ).add_to(m)

    return m

    # heatmap: https://autogis-site.readthedocs.io/en/latest/notebooks/L5/02_interactive-map-folium.html#heatmap


def render_map_to_file(file_in="plznito_cyklo.json", file_out="app/templates/map.html",
                       cluster=False):
    logger.info(f"loading data from {file_in}")
    data = json.load(open(file_in))
    logger.info(f"rendering map")
    map = get_map(data, cluster=cluster)
    map.save(file_out)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--log_level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--file_in", type=str, default="plznito_cyklo.json")
    parser.add_argument("--file_out", type=str, default="app/templates/map.html")
    parser.add_argument("--cluster_style", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(filename='plznito_monitoring.log',
                        level=logging.INFO,
                        format='%(asctime)s %(message)s')

    render_map_to_file(args.file_in, args.file_out, cluster=args.cluster_style)
