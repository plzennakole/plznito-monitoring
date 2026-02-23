import folium
from folium.plugins import MarkerCluster, Fullscreen
import json
import os
import datetime
import logging
import argparse
import tempfile
from collections import Counter
from html import escape
from urllib.parse import quote

logger = logging.getLogger(__name__)


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y",
)


def _parse_date_value(value):
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    return None


def _parse_item_datetime(item):
    date_candidates = []
    created = item.get("created")
    if isinstance(created, dict):
        date_candidates.append(created.get("date"))
    date_candidates.append(item.get("date"))

    for raw_date in date_candidates:
        parsed = _parse_date_value(raw_date)
        if parsed is not None:
            return parsed, str(raw_date).strip()
    return None, None


def _parse_coordinate(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() == "none":
            return None
        value = stripped
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_status_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _sanitize_photo_url(item):
    photos = item.get("photos")
    if not isinstance(photos, list) or not photos:
        return None
    photo = photos[0]
    if not isinstance(photo, dict):
        return None
    photo_url = photo.get("thumb")
    if not isinstance(photo_url, str):
        return None
    if not photo_url.startswith(("http://", "https://")):
        return None
    return photo_url


def _status_color(status_id):
    if status_id == 2:
        return "orange"
    if status_id == 3:
        return "green"
    if status_id == 6:
        return "lightgreen"
    return "red"


def _escape_multiline(value):
    return escape("" if value is None else str(value)).replace("\n", "<br>")


def _normalize_item(item):
    if not isinstance(item, dict):
        return None, "missing_required_fields"

    item_id = item.get("id")
    if item_id is None:
        return None, "missing_required_fields"

    status_id = _parse_status_id(item.get("status_id"))
    if status_id is None:
        return None, "missing_required_fields"

    date_time_obj, date_text = _parse_item_datetime(item)
    if date_time_obj is None:
        return None, "invalid_date"

    latitude = _parse_coordinate(item.get("latitude"))
    longitude = _parse_coordinate(item.get("longitude"))
    if latitude is None or longitude is None:
        return None, "invalid_coordinates"

    normalized = {
        "id": item_id,
        "status_id": status_id,
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "solution": item.get("solution"),
        "date_obj": date_time_obj,
        "date_text": date_text,
        "latitude": latitude,
        "longitude": longitude,
        "photo_url": _sanitize_photo_url(item),
    }
    return normalized, None


def _build_popup_html(item):
    ticket_id = str(item["id"])
    ticket_id_escaped = escape(ticket_id)
    ticket_url = f"https://www.plznito.cz/map#!/activity/{quote(ticket_id, safe='')}"
    name_html = _escape_multiline(item["name"])
    date_html = _escape_multiline(item["date_text"])
    description_html = _escape_multiline(item["description"])
    solution_html = _escape_multiline(item["solution"])

    popup_html = (
        f"<b>{name_html} "
        f"(<a href='{ticket_url}' target='_blank'>{ticket_id_escaped}</a>)</b><br>"
        f"{date_html}<br>{description_html}<br><br>{solution_html}<br>"
    )

    if item["photo_url"] is not None:
        popup_html += f"<img src='{escape(item['photo_url'], quote=True)}'>"

    return popup_html


def _make_layer(name, cluster=False, show=False):
    feature_group = folium.FeatureGroup(name=str(name), show=show)
    marker_target = feature_group
    if cluster:
        marker_target = MarkerCluster(name=str(name)).add_to(feature_group)
    return feature_group, marker_target


def get_map(data_current, cluster=False):
    m = folium.Map(location=[49.7443392, 13.3766164], zoom_start=13, control_scale=True)

    year_layers = {}
    year_targets = {}
    this_day = datetime.datetime.now()
    last_30_layer, last_30_target = _make_layer("Posledních 30 dnů", cluster=cluster, show=True)

    stats = Counter()
    stats["input_records"] = len(data_current)

    for item in data_current:
        normalized_item, error_reason = _normalize_item(item)
        if error_reason is not None:
            stats[f"skipped_{error_reason}"] += 1
            logger.debug("Skipping record due to %s: %r", error_reason, item)
            continue

        popup_html = _build_popup_html(normalized_item)
        popup = folium.Popup(popup_html, max_width=300, min_width=300)
        marker = folium.Marker(
            location=[normalized_item["latitude"], normalized_item["longitude"]],
            popup=popup,
            icon=folium.Icon(color=_status_color(normalized_item["status_id"]), icon="ok-sign"),
        )

        if this_day - datetime.timedelta(days=30) < normalized_item["date_obj"]:
            marker.add_to(last_30_target)
            stats["added_last_30_days"] += 1
        else:
            year = normalized_item["date_obj"].year
            if year not in year_targets:
                year_layer, year_target = _make_layer(str(year), cluster=cluster, show=False)
                year_layers[year] = year_layer
                year_targets[year] = year_target
            marker.add_to(year_targets[year])
            stats["added_year_layer"] += 1

    last_30_layer.add_to(m)
    for year in sorted(year_layers):
        year_layers[year].add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    logger.info(
        (
            "Map processing summary: input=%d, added_last_30=%d, added_year_layer=%d, "
            "skipped_missing_required_fields=%d, skipped_invalid_date=%d, skipped_invalid_coordinates=%d."
        ),
        stats["input_records"],
        stats["added_last_30_days"],
        stats["added_year_layer"],
        stats["skipped_missing_required_fields"],
        stats["skipped_invalid_date"],
        stats["skipped_invalid_coordinates"],
    )

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
    logger.info("Loading data from %s", file_in)
    with open(file_in, encoding="utf-8") as fr:
        data = json.load(fr)

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data_records = data["items"]
    elif isinstance(data, list):
        data_records = data
    else:
        raise ValueError("Input JSON must be a list of records or an object with an 'items' list.")

    logger.info("Rendering map from %d records", len(data_records))
    map_obj = get_map(data_records, cluster=cluster)

    target_dir = os.path.dirname(os.path.abspath(file_out))
    os.makedirs(target_dir, exist_ok=True)

    temp_path = None
    fd, temp_path = tempfile.mkstemp(prefix=".tmp_map_", suffix=".html", dir=target_dir)
    os.close(fd)
    try:
        map_obj.save(temp_path)
        os.replace(temp_path, file_out)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    logger.info("Saved map to %s", file_out)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("--log_level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--file_in", type=str, default="plznito_cyklo.json")
    parser.add_argument("--file_out", type=str, default="app/templates/map.html")
    parser.add_argument("--cluster_style", action="store_true")
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(filename='plznito_monitoring.log',
                        level=log_level,
                        format='%(asctime)s %(message)s')
    logger.setLevel(log_level)

    render_map_to_file(args.file_in, args.file_out, cluster=args.cluster_style)
