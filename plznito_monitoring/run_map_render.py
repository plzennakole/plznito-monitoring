import json
import os
import datetime
import logging
import argparse
import tempfile
import re
from collections import Counter
from urllib.parse import quote

logger = logging.getLogger(__name__)


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y",
)
DEFAULT_CENTER = [49.7443392, 13.3766164]
DEFAULT_ZOOM = 13
DEFAULT_TIME_FILTER = "last_30_days"
STATUS_COLOR_MAP = {
    2: "orange",
    3: "green",
    6: "lightgreen",
}
STATUS_LABEL_MAP = {
    2: "V řešení",
    3: "Vyřešeno",
    6: "Odpovězeno",
}


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


def _parse_optional_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _parse_status_id(value):
    return _parse_optional_int(value)


def _extract_optional_label(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
        return None
    if isinstance(value, dict):
        for key in ("name", "title", "label"):
            raw = value.get(key)
            if not isinstance(raw, str):
                continue
            stripped = raw.strip()
            if stripped:
                return stripped
    return None


def _status_label_for_filter(status_id):
    label = STATUS_LABEL_MAP.get(status_id)
    if label:
        return label
    return f"Stav {status_id}"


def _resolve_category_filter(category_id, category_value):
    if category_id is not None:
        return f"id:{category_id}", f"Kategorie {category_id}"

    category_name = _extract_optional_label(category_value)
    if category_name is not None:
        return f"name:{category_name}", category_name

    return "unknown", "Neznámá kategorie"


def _to_unix_timestamp(value):
    if value.tzinfo is None:
        return int(value.replace(tzinfo=datetime.timezone.utc).timestamp())
    return int(value.astimezone(datetime.timezone.utc).timestamp())


def _sanitize_photo_url(item):
    photos = item.get("photos")
    if not isinstance(photos, list) or not photos:
        return None
    photo = photos[0]

    # Handle case where photo is a dictionary with "thumb" key
    if isinstance(photo, dict):
        photo_url = photo.get("thumb")
        if not isinstance(photo_url, str):
            return None
        if not photo_url.startswith(("http://", "https://")):
            return None
        return photo_url

    # Handle case where photo is a string path
    if isinstance(photo, str):
        photo_url = photo.strip()
        if not photo_url:
            return None
        # If it's a relative path, prepend the base URL
        if not photo_url.startswith(("http://", "https://")):
            photo_url = f"https://tf-prod-plznito-web.s3.eu-central-1.amazonaws.com/{photo_url}"
        return photo_url

    return None


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

    category_id = _parse_optional_int(item.get("category_id"))
    category_key, category_label = _resolve_category_filter(category_id, item.get("category"))

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
        "category_id": category_id,
        "category_key": category_key,
        "category_label": category_label,
    }
    return normalized, None


def _item_id_for_skip(item):
    if not isinstance(item, dict):
        return None
    item_id = item.get("id")
    if item_id is None:
        return None
    return str(item_id)


def _serialize_marker(item, popup_mode):
    ticket_id = str(item["id"])
    marker_data = {
        "id": ticket_id,
        "status_id": item["status_id"],
        "status_label": _status_label_for_filter(item["status_id"]),
        "category_key": item["category_key"],
        "category_label": item["category_label"],
        "name": "" if item["name"] is None else str(item["name"]),
        "date_text": item["date_text"],
        "date_ts": _to_unix_timestamp(item["date_obj"]),
        "year": item["date_obj"].year,
        "lat": item["latitude"],
        "lon": item["longitude"],
        "ticket_url": f"https://www.plznito.cz/map/{quote(ticket_id, safe='')}",
    }

    if popup_mode == "full":
        marker_data["description"] = "" if item["description"] is None else str(item["description"])
        marker_data["solution"] = "" if item["solution"] is None else str(item["solution"])
        marker_data["photo_url"] = item["photo_url"]

    return marker_data


def _status_option_sort_key(option):
    key = option["key"]
    return int(key) if key.isdigit() else 10**9


def _category_option_sort_key(option):
    key = option["key"]
    if key.startswith("id:"):
        category_id = key.split(":", 1)[1]
        if category_id.isdigit():
            return 0, int(category_id)
        return 0, category_id
    if key.startswith("name:"):
        return 1, option["label"].lower()
    return 2, option["label"].lower()


def serialize_map_data(data_current, popup_mode="compact", now=None):
    if popup_mode not in {"compact", "full"}:
        raise ValueError("popup_mode must be 'compact' or 'full'.")

    if now is None:
        now = datetime.datetime.now()

    recent_cutoff_7 = now - datetime.timedelta(days=7)
    recent_cutoff_30 = now - datetime.timedelta(days=30)
    stats = Counter()
    markers = []
    skipped = []
    years = set()
    status_options_map = {}
    category_options_map = {}

    for item in data_current:
        stats["input_records"] += 1

        normalized_item, error_reason = _normalize_item(item)
        if error_reason is not None:
            stats[f"skipped_{error_reason}"] += 1
            skipped.append({"id": _item_id_for_skip(item), "reason": error_reason})
            logger.debug("Skipping record due to %s: %r", error_reason, item)
            continue

        marker_data = _serialize_marker(normalized_item, popup_mode=popup_mode)

        if normalized_item["date_obj"] > recent_cutoff_7:
            stats["added_last_7_days"] += 1

        if normalized_item["date_obj"] > recent_cutoff_30:
            marker_data["layer"] = "recent"
            stats["added_last_30_days"] += 1
        else:
            year = normalized_item["date_obj"].year
            marker_data["layer"] = str(year)
            years.add(year)
            stats["added_year_layer"] += 1

        status_key = str(marker_data["status_id"])
        if status_key not in status_options_map:
            status_options_map[status_key] = {
                "key": status_key,
                "label": marker_data["status_label"],
            }

        category_key = marker_data["category_key"]
        if category_key not in category_options_map:
            category_options_map[category_key] = {
                "key": category_key,
                "label": marker_data["category_label"],
            }

        markers.append(marker_data)

    stats["valid_rendered"] = len(markers)
    stats["skipped_total"] = stats["input_records"] - stats["valid_rendered"]
    status_options = sorted(status_options_map.values(), key=_status_option_sort_key)
    category_options = sorted(category_options_map.values(), key=_category_option_sort_key)

    return {
        "markers": markers,
        "years": sorted(years),
        "status_options": status_options,
        "category_options": category_options,
        "skipped": skipped,
        "stats": stats,
        "popup_mode": popup_mode,
        "generated_ts": _to_unix_timestamp(now),
    }


def _json_for_inline_script(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _minify_html(html_text):
    block_pattern = re.compile(r"(?is)<(script|style|pre|textarea)\b.*?</\1>")
    preserved_blocks = []

    def _preserve_block(match):
        block_id = len(preserved_blocks)
        preserved_blocks.append(match.group(0))
        return f"__HTML_MINIFY_BLOCK_{block_id}__"

    minified = block_pattern.sub(_preserve_block, html_text)
    minified = re.sub(r"<!--(?!\s*\[if).*?-->", "", minified, flags=re.DOTALL)
    minified = re.sub(r">\s+<", "><", minified)
    minified = minified.strip()

    for block_id, block_text in enumerate(preserved_blocks):
        minified = minified.replace(f"__HTML_MINIFY_BLOCK_{block_id}__", block_text)

    return minified


def _build_map_html(serialized_data, cluster=False):
    markers_json = _json_for_inline_script(serialized_data["markers"])
    years_json = _json_for_inline_script(serialized_data["years"])
    status_options_json = _json_for_inline_script(serialized_data["status_options"])
    category_options_json = _json_for_inline_script(serialized_data["category_options"])
    popup_mode_json = _json_for_inline_script(serialized_data["popup_mode"])
    status_colors_json = _json_for_inline_script({str(key): value for key, value in STATUS_COLOR_MAP.items()})
    status_labels_json = _json_for_inline_script({str(key): value for key, value in STATUS_LABEL_MAP.items()})
    generated_ts_json = _json_for_inline_script(serialized_data["generated_ts"])
    use_cluster = "true" if cluster else "false"

    return f"""<!-- Generated by run_map_render.py -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.2.0/css/bootstrap.min.css">
<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/font-awesome/4.6.3/css/font-awesome.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/python-visualization/folium/folium/templates/leaflet.awesome.rotate.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.Default.css">
<style>
  #plznito-map-wrapper {{
    position: relative;
  }}
  #plznito-map {{
    width: 100%;
    min-height: 520px;
    height: calc(100vh - 32px);
  }}
  .plznito-overlay {{
    position: fixed;
    z-index: 9999;
    background: #fff;
    border: 2px solid #808080;
    border-radius: 4px;
    padding: 8px 10px;
    font-size: 13px;
    line-height: 1.3;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }}
  #plznito-legend {{
    top: 50px;
    left: 50px;
    width: 215px;
  }}
  #plznito-filters {{
    top: 50px;
    right: 50px;
    width: 280px;
    max-height: calc(100vh - 80px);
    overflow-y: auto;
  }}
  .plznito-filter-section {{
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #d9d9d9;
  }}
  .plznito-filter-option {{
    display: block;
    margin: 2px 0;
    font-weight: normal;
  }}
  .plznito-filter-subtitle {{
    font-weight: 600;
    margin-bottom: 4px;
  }}
  .plznito-filter-actions {{
    margin-top: 8px;
  }}
  #plznito-filter-time {{
    width: 100%;
    max-width: 100%;
  }}
  #plznito-filter-count {{
    margin-top: 6px;
    font-size: 12px;
    color: #444;
  }}
  .plznito-dot {{
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    margin-right: 6px;
  }}
  .leaflet-popup-content {{
    font-size: 12px;
  }}
  .leaflet-popup-content .plznito-popup-photo {{
    display: block;
    width: 100%;
    max-width: 100%;
    max-height: 180px;
    height: auto;
    margin-top: 6px;
    border-radius: 4px;
    object-fit: contain;
  }}
  @media (max-width: 860px) {{
    #plznito-map {{
      min-height: 420px;
      height: 72vh;
    }}
    #plznito-legend {{
      position: static;
      width: auto;
      margin: 8px 0;
    }}
    #plznito-filters {{
      position: static;
      width: auto;
      max-height: none;
      margin: 8px 0;
      overflow-y: visible;
    }}
  }}
</style>
<div id="plznito-map-wrapper">
  <div id="plznito-map"></div>
  <div id="plznito-legend" class="plznito-overlay">
    <strong>Legenda</strong><br>
    <span class="plznito-dot" style="background: green;"></span>Vyřešeno<br>
    <span class="plznito-dot" style="background: lightgreen;"></span>Odpovězeno<br>
    <span class="plznito-dot" style="background: orange;"></span>V řešení<br>
    <span class="plznito-dot" style="background: red;"></span>Odmítnuto / nevyřešeno
  </div>
  <div id="plznito-filters" class="plznito-overlay">
    <strong>Filtry</strong>
    <div class="plznito-filter-section">
      <div class="plznito-filter-subtitle">Období</div>
      <select id="plznito-filter-time"></select>
    </div>
    <div class="plznito-filter-section">
      <div class="plznito-filter-subtitle">Stav</div>
      <div id="plznito-filter-status-list"></div>
    </div>
    <div class="plznito-filter-section">
      <div class="plznito-filter-subtitle">Kategorie</div>
      <div id="plznito-filter-category-list"></div>
    </div>
    <div class="plznito-filter-actions">
      <button id="plznito-filter-reset" type="button" class="btn btn-default btn-xs">Reset filtrů</button>
      <div id="plznito-filter-count"></div>
    </div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/leaflet.markercluster.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>
<script>
(function () {{
  const markers = {markers_json};
  const years = {years_json};
  const statusOptions = {status_options_json};
  const categoryOptions = {category_options_json};
  const generatedTs = {generated_ts_json};
  const popupMode = {popup_mode_json};
  const useCluster = {use_cluster};
  const defaultTimeFilter = "{DEFAULT_TIME_FILTER}";

  const statusColors = {status_colors_json};
  const statusLabels = {status_labels_json};

  function colorForStatus(statusId) {{
    return statusColors[String(statusId)] || "red";
  }}

  function labelForStatus(statusId) {{
    return statusLabels[String(statusId)] || "Odmítnuto / nevyřešeno";
  }}

  function escapeHtml(value) {{
    const text = value === null || value === undefined ? "" : String(value);
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}

  function escapeMultiline(value) {{
    return escapeHtml(value).replace(/\\n/g, "<br>");
  }}

  function buildPopupContent(marker) {{
    let popupHtml =
      "<b>" + escapeMultiline(marker.name) + " " +
      "(<a href='" + escapeHtml(marker.ticket_url) + "' target='_blank' rel='noopener noreferrer'>" +
      escapeHtml(marker.id) + "</a>)</b><br>" +
      escapeMultiline(marker.date_text) + "<br>" +
      escapeHtml(marker.status_label || labelForStatus(marker.status_id));

    if (popupMode === "full") {{
      popupHtml += "<br>" + escapeMultiline(marker.description || "");

      if ((marker.solution || "").trim()) {{
        popupHtml += "<br><br>" + escapeMultiline(marker.solution);
      }}

      if (marker.photo_url) {{
        popupHtml +=
          "<br><img class='plznito-popup-photo' src='" +
          escapeHtml(marker.photo_url) +
          "' alt='Fotografie hlášení'>";
      }}
    }}
    return popupHtml;
  }}

  if (!window.L) {{
    throw new Error("Leaflet library is not available.");
  }}

  const map = L.map("plznito-map", {{ zoomControl: true }}).setView({DEFAULT_CENTER}, {DEFAULT_ZOOM});
  L.control.scale().addTo(map);
  L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    attribution: "Data by &copy; <a href='http://openstreetmap.org'>OpenStreetMap</a>, under <a href='http://www.openstreetmap.org/copyright'>ODbL</a>.",
    maxZoom: 18
  }}).addTo(map);

  const displayLayer = useCluster ? L.markerClusterGroup() : L.layerGroup();
  displayLayer.addTo(map);

  const markerEntries = markers.map(function (marker) {{
    const markerIcon = L.AwesomeMarkers.icon({{
      icon: "ok-sign",
      prefix: "glyphicon",
      iconColor: "white",
      markerColor: colorForStatus(marker.status_id)
    }});
    const markerObj = L.marker([marker.lat, marker.lon], {{ icon: markerIcon }});
    markerObj.bindPopup(buildPopupContent(marker), {{ maxWidth: 300, minWidth: 300 }});
    return {{ marker: marker, markerObj: markerObj }};
  }});

  function appendCheckboxFilters(containerId, options, idPrefix, dataAttribute) {{
    const container = document.getElementById(containerId);
    container.innerHTML = "";

    if (!options.length) {{
      container.textContent = "(žádné hodnoty)";
      return;
    }}

    options.forEach(function (option, index) {{
      const optionId = idPrefix + index;
      const label = document.createElement("label");
      label.className = "plznito-filter-option";
      label.setAttribute("for", optionId);

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = optionId;
      checkbox.checked = true;
      checkbox.value = option.key;
      checkbox.setAttribute(dataAttribute, option.key);

      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(" " + option.label));
      container.appendChild(label);
    }});
  }}

  function buildTimeFilterOptions() {{
    const select = document.getElementById("plznito-filter-time");
    const options = [
      {{ value: "last_7_days", label: "Posledních 7 dnů" }},
      {{ value: "last_30_days", label: "Posledních 30 dnů" }},
      {{ value: "all_time", label: "Všechna hlášení" }}
    ];

    years.forEach(function (year) {{
      options.push({{ value: "year:" + String(year), label: "Rok " + String(year) }});
    }});

    select.innerHTML = "";
    options.forEach(function (option) {{
      const optionEl = document.createElement("option");
      optionEl.value = option.value;
      optionEl.textContent = option.label;
      select.appendChild(optionEl);
    }});
    select.value = defaultTimeFilter;
  }}

  function selectedKeys(selector, attributeName) {{
    const keys = new Set();
    document.querySelectorAll(selector).forEach(function (input) {{
      const value = input.getAttribute(attributeName);
      if (value !== null) {{
        keys.add(value);
      }}
    }});
    return keys;
  }}

  function matchesTimeFilter(marker, filterValue) {{
    if (filterValue === "last_7_days") {{
      return marker.date_ts >= generatedTs - (7 * 24 * 60 * 60);
    }}
    if (filterValue === "last_30_days") {{
      return marker.date_ts >= generatedTs - (30 * 24 * 60 * 60);
    }}
    if (filterValue === "all_time") {{
      return true;
    }}
    if (filterValue.indexOf("year:") === 0) {{
      return String(marker.year) === filterValue.slice(5);
    }}
    return true;
  }}

  function applyFilters() {{
    const timeFilterElement = document.getElementById("plznito-filter-time");
    const selectedTime = timeFilterElement ? timeFilterElement.value : defaultTimeFilter;
    const selectedStatuses = selectedKeys("input[id^='plznito-filter-status-']:checked", "data-status-key");
    const selectedCategories = selectedKeys("input[id^='plznito-filter-category-']:checked", "data-category-key");

    displayLayer.clearLayers();
    let shownCount = 0;

    markerEntries.forEach(function (entry) {{
      const marker = entry.marker;
      if (!matchesTimeFilter(marker, selectedTime)) {{
        return;
      }}
      if (!selectedStatuses.has(String(marker.status_id))) {{
        return;
      }}
      if (!selectedCategories.has(marker.category_key)) {{
        return;
      }}
      entry.markerObj.addTo(displayLayer);
      shownCount += 1;
    }});

    const counterElement = document.getElementById("plznito-filter-count");
    counterElement.textContent = "Zobrazeno: " + shownCount + " / " + markerEntries.length;
  }}

  function resetFilters() {{
    const timeFilterElement = document.getElementById("plznito-filter-time");
    if (timeFilterElement) {{
      timeFilterElement.value = defaultTimeFilter;
    }}

    document.querySelectorAll("input[id^='plznito-filter-status-'], input[id^='plznito-filter-category-']").forEach(
      function (input) {{
        input.checked = true;
      }}
    );
    applyFilters();
  }}

  buildTimeFilterOptions();
  appendCheckboxFilters(
    "plznito-filter-status-list",
    statusOptions,
    "plznito-filter-status-",
    "data-status-key"
  );
  appendCheckboxFilters(
    "plznito-filter-category-list",
    categoryOptions,
    "plznito-filter-category-",
    "data-category-key"
  );

  document.getElementById("plznito-filter-time").addEventListener("change", applyFilters);
  document.querySelectorAll("input[id^='plznito-filter-status-'], input[id^='plznito-filter-category-']").forEach(
    function (input) {{
      input.addEventListener("change", applyFilters);
    }}
  );
  document.getElementById("plznito-filter-reset").addEventListener("click", resetFilters);

  applyFilters();
}})();
</script>
"""


def get_map(data_current, cluster=False, popup_mode="compact"):
    serialized_data = serialize_map_data(data_current, popup_mode=popup_mode)
    stats = serialized_data["stats"]

    logger.info(
        (
            "Map processing summary: input=%d, valid_rendered=%d, added_last_7=%d, added_last_30=%d, "
            "added_year_layer=%d, skipped_total=%d, skipped_missing_required_fields=%d, "
            "skipped_invalid_date=%d, skipped_invalid_coordinates=%d."
        ),
        stats["input_records"],
        stats["valid_rendered"],
        stats["added_last_7_days"],
        stats["added_last_30_days"],
        stats["added_year_layer"],
        stats["skipped_total"],
        stats["skipped_missing_required_fields"],
        stats["skipped_invalid_date"],
        stats["skipped_invalid_coordinates"],
    )

    return _build_map_html(serialized_data, cluster=cluster)


def render_map_to_file(file_in="plznito_cyklo.json", file_out="app/templates/map.html",
                       cluster=False, popup_mode="compact"):
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
    map_html = get_map(data_records, cluster=cluster, popup_mode=popup_mode)
    map_html = _minify_html(map_html)

    target_dir = os.path.dirname(os.path.abspath(file_out))
    os.makedirs(target_dir, exist_ok=True)

    temp_path = None
    fd, temp_path = tempfile.mkstemp(prefix=".tmp_map_", suffix=".html", dir=target_dir)
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as fw:
            fw.write(map_html)
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
    parser.add_argument("--popup_mode", type=str, default="compact", choices=["compact", "full"])
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(filename='plznito_monitoring.log',
                        level=log_level,
                        format='%(asctime)s %(message)s')
    logger.setLevel(log_level)

    render_map_to_file(args.file_in, args.file_out, cluster=args.cluster_style, popup_mode=args.popup_mode)
