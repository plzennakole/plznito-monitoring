import json
from flask import Flask, render_template
import folium
from folium.plugins import MarkerCluster, Fullscreen
import ast
import json
import os
import datetime
import logging

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

from app import app

try: ipynb_path
except NameError: ipynb_path = os.getcwd()


def get_map(data_current):
    m = folium.Map(location=[49.7, 13.4], zoom_start=13,  control_scale=True)

    # make groups for the years
    years = {}
    this_year = datetime.datetime.now().year
    for y in range(2015, this_year):
        years[y] = folium.FeatureGroup(name=str(y), show=False)
    years[this_year] = folium.FeatureGroup(name=str(this_year), show=True)

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
        #print(item["solution"])
        solution = str(item['solution']).replace('\n', '<br>')

        text = f"<b>{item['name']} ({item['id']})</b><br>{date}<br>{description}<br><br>{solution}<br>"

        if len(item['photos']) > 0:
            text += f"<img src='{item['photos'][0]['thumb'].replace('https', 'http')}'>"

        popup = folium.Popup(text, max_width=300, min_width=300)
        folium.Marker(
            location=[item["latitude"], item["longitude"]],
            popup=popup,
            icon=folium.Icon(color=color, icon="ok-sign"),
        ).add_to(years[date_time_obj.year])

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

    #plus_button_html = '''<a href="#" class="w3-button w3-large w3-circle w3-green w3-ripple" style="position: fixed;
    #     top: 50px; left: 50px; z-index:9999;">+</a>'''
    #m.get_root().html.add_child(folium.Element(plus_button_html))
    # plus overlay form to get new points to map
    # https://morioh.com/p/f23f87a146b4

    Fullscreen(position='topright',  # ‘topleft’, default=‘topright’, ‘bottomleft’, ‘bottomright’
                       title='FULL SCREEN ON',
                       title_cancel='FULL SCREEN OFF',
                       force_separate_button=True
                       ).add_to(m)

    return m

    # heatmap: https://autogis-site.readthedocs.io/en/latest/notebooks/L5/02_interactive-map-folium.html#heatmap


def render_map_to_file():
    logging.info(f"loading data from .json")
    data = json.load(open("plznito_cyklo.json"))
    logging.info(f"rendering map")
    map = get_map(data)
    map.save('app/templates/map.html')
    return True


@app.route('/')
def index():
    #logging.info(f"loading data from .json")
    #data = json.load(open("plznito_cyklo.json"))
    #logging.info(f"rendering map")
    #map = get_map(data)
    #map.save(os.path.join(os.tmpdir(), 'app/templates/map.html'))
    #return map._repr_html_()

    # save computations
    return render_template('index.html')
