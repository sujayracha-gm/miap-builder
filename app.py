"""
River & Site Mapper — a no-code map builder for proposals.

Workflow:
1. Set the map area (search a location or enter bounds)
2. Add rivers (auto-fetch from USGS) and optional watershed
3. Add markers via table edit OR clicking the preview map
4. Drag markers / fine-tune labels
5. Export high-resolution PNG

Built for non-technical users. Single-file Streamlit app.
"""

import io
import json
import math
from typing import Optional

import contextily as cx
import folium
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from matplotlib.lines import Line2D
from matplotlib_scalebar.scalebar import ScaleBar
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union
from streamlit_folium import st_folium

# ---------------- App config ----------------
st.set_page_config(page_title="River & Site Mapper", page_icon="🗺️", layout="wide")

# Soften default Streamlit chrome
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.5rem;}
      [data-testid="stSidebar"] {background-color: #fafafa;}
      h1, h2, h3 {font-weight: 500;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🗺️ River & Site Mapper")
st.caption("Build publication-quality maps for proposals — no GIS skills needed.")

with st.expander("📖 How to use this app", expanded=False):
    st.markdown(
        """
        ### Quick workflow

        **0. Enter your email** *(sidebar, top)*
        Required for the location search. We send your email as a contact string to
        OpenStreetMap (the free service that powers the search). No account needed —
        it's just so they can reach you in the rare case of a problem with your search activity.

        **1. Set the map area** *(sidebar)*
        Search a city or address (e.g. *Pasadena, CA*) or paste coordinates as `lat, lon`
        (e.g. `34.05, -118.24`). For tighter framing, search a more specific location.

        **2. Add rivers** *(sidebar)*
        - **Get rivers (US)** — USGS NHDPlus, high quality, US only.
        - **Get rivers (worldwide)** — OpenStreetMap, slower but works globally.
        After loading, expand *Filter to specific rivers* to keep only the ones you want.

        **3. Add watershed boundaries** *(US only)*
        On the **Preview** tab, click any point on the map (ideally on a river).
        Then in the sidebar, give the watershed a name and click *➕ Add watershed at clicked point* —
        the upstream watershed for that location loads automatically. Repeat for each
        watershed you want to add. To remove one, click the ✕ next to its name.

        **4. Add markers** *(Markers tab)*
        Three ways:
        - **Quick add** — one marker at a time via the form.
        - **Paste a table** — copy from Excel/Google Sheets with columns
          `name, lat, lon, category` (header row required). Tab- or comma-separated both work.
        - **Edit the table directly** — click any cell to edit, or use the "+" at the bottom
          to add a row. Check the box on the left of a row and press *Delete* to remove it.

        **5. Style your markers** *(Markers tab → 🎨 Category styles)*
        Pick the shape (star, circle, square, triangle, diamond), color, and size for each
        category. Markers in the same category share a style.

        **6. Position labels** *(Markers tab table)*
        - **Label anchor** — coarse position relative to the marker
          (right, left, above, below, above-right, etc.).
        - **Label x-offset / y-offset** — fine-tune in **meters**. Positive x pushes right,
          positive y pushes up. Use this to nudge labels apart when they overlap.

        **7. Preview** *(Preview tab)*
        - The interactive map shows your markers with popups (click a marker to see details).
        - Click *🖼️ Render high-res preview* to see what the exported map will look like.

        **8. Export** *(Export tab)*
        - Toggle legend, scale bar, north arrow on/off.
        - Pick a basemap style.
        - Choose DPI: 300 for documents, 600 for print-quality.
        - Click *📥 Generate PNG*, then *⬇️ Download PNG*.

        ---

        ### Saving and resuming work

        On the **Export** tab, *Save project to JSON* downloads everything (markers, rivers,
        watershed, styles) into one file. Use *Load project from JSON* to pick up exactly
        where you left off — useful when iterating on the same map across days.

        ---

        ### Tips

        - **Labels overlap?** Set different *Label anchor* values, or use small x/y offsets
          (try 1500–3000 meters at first; the right value depends on map scale).
        - **Map area too big or small?** Re-search with a more specific location.
          *Pasadena* is tighter than *California*.
        - **River fetch is slow or fails?** Try a smaller area, or switch between
          USGS and OpenStreetMap.
        - **Watershed fetch fails?** Make sure your clicked point is in the US and
          near a known river. Click as close to a stream as possible.
        - **Want to change the map area?** Re-search in the sidebar — your markers stay put.
        """
    )

# ---------------- Session state defaults ----------------
def init_state():
    defaults = {
        "markers": pd.DataFrame(
            columns=["name", "lat", "lon", "category", "label_dx", "label_dy", "label_pos"]
        ),
        "map_bounds": None,        # (south, west, north, east) in WGS84
        "rivers_geojson": None,    # GeoJSON dict
        "watersheds": [],          # list of dicts: {"name": str, "geojson": dict, "outlet": (lon, lat)}
        "river_query_result": None,
        "search_query": "",
        "last_clicked": None,
        "category_styles": {
            "Treatment facility": {"shape": "star", "color": "#FFD23F", "size": 420},
            "Sample site":         {"shape": "circle", "color": "#1D9E75", "size": 110},
            "Other":               {"shape": "square", "color": "#D85A30", "size": 130},
        },
        "show_legend": True,
        "show_scalebar": True,
        "show_north_arrow": True,
        "basemap": "CartoDB.Voyager",
        "user_email": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ---------------- Geocoding & data fetch ----------------
# Nominatim (OpenStreetMap's free geocoder) asks for a unique User-Agent
# identifying who's making requests. We collect the user's email at session
# start and include it. No account or signup required — it's just a contact
# string in case OSM needs to reach the user about abuse.
# Policy: https://operations.osmfoundation.org/policies/nominatim/

def _build_user_agent():
    email = st.session_state.get("user_email", "").strip() or "anonymous@river-site-mapper"
    return f"river-site-mapper/1.0 ({email})"


@st.cache_data(show_spinner=False, ttl=3600)
def geocode(query: str, ua: str):
    """Look up a place name. Tries Nominatim first, then Photon as a fallback.

    `ua` is included in the cache key so different users get separate caches.
    """
    import time

    # ---- Try Nominatim (primary) ----
    try:
        # Nominatim asks for ≤1 req/sec — small sleep helps when users click fast
        time.sleep(1.1)
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": ua, "Accept-Language": "en"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            item = r.json()[0]
            return {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "bbox": [float(x) for x in item["boundingbox"]],
                "display_name": item["display_name"],
            }
        # If 429 or empty, fall through to fallback
    except Exception:
        pass  # try fallback

    # ---- Fallback: Photon (Komoot, also free, no rate limit issues) ----
    try:
        r = requests.get(
            "https://photon.komoot.io/api",
            params={"q": query, "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("features"):
            return None
        feat = data["features"][0]
        lon, lat = feat["geometry"]["coordinates"]
        props = feat["properties"]
        # Photon doesn't return a bbox by default; build one ~50km wide
        d = 0.5
        if "extent" in props:
            # extent is [minLon, maxLat, maxLon, minLat]
            ext = props["extent"]
            bbox = [ext[3], ext[1], ext[0], ext[2]]  # [s, n, w, e]
        else:
            bbox = [lat - d, lat + d, lon - d, lon + d]
        name_parts = [props.get(k) for k in ("name", "city", "state", "country") if props.get(k)]
        return {
            "lat": lat,
            "lon": lon,
            "bbox": bbox,
            "display_name": ", ".join(name_parts) or query,
        }
    except Exception as e:
        st.error(f"Geocoding failed (both Nominatim and Photon): {e}")
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_rivers_usgs(min_lon, min_lat, max_lon, max_lat):
    """Fetch named rivers/streams from EPA WATERS NHDPlus (covers US)."""
    try:
        url = "https://watersgeo.epa.gov/arcgis/rest/services/NHDPlus/NHDPlus/MapServer/2/query"
        params = {
            "where": "gnis_name IS NOT NULL",
            "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "gnis_name,streamorder",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultRecordCount": "2000",
        }
        r = requests.get(url, params=params, timeout=30,
                         headers={"User-Agent": "river-site-mapper/1.0"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.warning(f"USGS river fetch failed: {e}. Try a smaller area.")
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_rivers_osm(min_lon, min_lat, max_lon, max_lat):
    """Fetch rivers from OpenStreetMap via Overpass API (worldwide)."""
    try:
        bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
        query = f"""
        [out:json][timeout:30];
        (way["waterway"~"^(river|stream|canal)$"]({bbox}););
        out geom;
        """
        r = requests.post("https://overpass-api.de/api/interpreter",
                         data={"data": query}, timeout=45)
        r.raise_for_status()
        data = r.json()
        # Convert to GeoJSON
        features = []
        for el in data.get("elements", []):
            if "geometry" not in el:
                continue
            coords = [[g["lon"], g["lat"]] for g in el["geometry"]]
            if len(coords) < 2:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "gnis_name": el.get("tags", {}).get("name", ""),
                    "waterway": el.get("tags", {}).get("waterway", "")
                },
                "geometry": {"type": "LineString", "coordinates": coords}
            })
        return {"type": "FeatureCollection", "features": features}
    except Exception as e:
        st.warning(f"OSM river fetch failed: {e}.")
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_watershed_usgs(lon: float, lat: float):
    """Get the upstream watershed for a point, from USGS Water Services."""
    try:
        # USGS NLDI service - delineate basin upstream of a point
        url = (f"https://api.water.usgs.gov/nldi/linked-data/comid/position"
               f"?coords=POINT({lon} {lat})&f=json")
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "river-site-mapper/1.0"})
        r.raise_for_status()
        comid_data = r.json()
        if not comid_data.get("features"):
            return None
        comid = comid_data["features"][0]["properties"]["comid"]

        url2 = f"https://api.water.usgs.gov/nldi/linked-data/comid/{comid}/basin?f=json"
        r2 = requests.get(url2, timeout=30,
                          headers={"User-Agent": "river-site-mapper/1.0"})
        r2.raise_for_status()
        return r2.json()
    except Exception as e:
        st.warning(f"Watershed fetch failed: {e}. Note: USGS watersheds only cover the US.")
        return None


# ---------------- Sidebar: map area & data ----------------
with st.sidebar:
    st.header("👤 Your info")
    st.session_state.user_email = st.text_input(
        "Your email",
        value=st.session_state.get("user_email", ""),
        placeholder="you@example.com",
        help=(
            "Required for the location search. We send this as a contact string "
            "to OpenStreetMap so they can reach you if there's a problem with "
            "your search activity. No account or signup is needed."
        ),
    )
    if not st.session_state.user_email.strip():
        st.caption("⚠️ Add your email above before using location search.")
    st.divider()

    st.header("1. Map area")
    search = st.text_input(
        "Search location",
        value=st.session_state.get("search_query", ""),
        placeholder="e.g. Los Angeles or 34.05, -118.24",
        help="Type a city, address, or 'lat, lon' to center the map there.",
    )
    if st.button("Search", use_container_width=True):
        st.session_state.search_query = search
        # Try lat,lon first
        try:
            parts = [float(x.strip()) for x in search.split(",")]
            if len(parts) == 2:
                lat, lon = parts
                d = 0.5  # ~50km default
                st.session_state.map_bounds = (lat-d, lon-d, lat+d, lon+d)
                st.success(f"Centered on ({lat:.4f}, {lon:.4f})")
        except Exception:
            if not st.session_state.user_email.strip():
                st.error("Please add your email at the top of the sidebar before searching.")
            else:
                result = geocode(search, _build_user_agent())
                if result:
                    s, n, w, e = result["bbox"]
                    st.session_state.map_bounds = (s, w, n, e)
                    st.success(f"Found: {result['display_name'][:60]}")
                else:
                    st.error("Location not found. Try a different search.")

    st.divider()
    st.header("2. Add layers")

    if st.session_state.map_bounds is None:
        st.info("Set a map area first.")
    else:
        s, w, n, e = st.session_state.map_bounds

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🌊 Get rivers (US)", use_container_width=True,
                         help="Fetches named streams from USGS NHDPlus (US only)."):
                with st.spinner("Fetching rivers from USGS..."):
                    st.session_state.rivers_geojson = fetch_rivers_usgs(w, s, e, n)
                    if st.session_state.rivers_geojson:
                        n_feats = len(st.session_state.rivers_geojson.get("features", []))
                        st.success(f"Loaded {n_feats} river segments.")
        with col2:
            if st.button("🌐 Get rivers (worldwide)", use_container_width=True,
                         help="Fetches rivers from OpenStreetMap (slower but works anywhere)."):
                with st.spinner("Fetching rivers from OpenStreetMap..."):
                    st.session_state.rivers_geojson = fetch_rivers_osm(w, s, e, n)
                    if st.session_state.rivers_geojson:
                        n_feats = len(st.session_state.rivers_geojson.get("features", []))
                        st.success(f"Loaded {n_feats} river segments.")

        # Filter rivers by name
        if st.session_state.rivers_geojson:
            names = sorted({
                f["properties"].get("gnis_name", "")
                for f in st.session_state.rivers_geojson.get("features", [])
                if f["properties"].get("gnis_name", "")
            })
            if names:
                with st.expander(f"Filter to specific rivers ({len(names)} available)"):
                    selected = st.multiselect(
                        "Show only these rivers (leave empty to show all)",
                        options=names,
                        key="selected_rivers",
                    )

        st.divider()
        st.subheader("Watersheds (US only)")
        st.caption(
            "Click the preview map to set an outlet point, then add a watershed below. "
            "You can add as many as you need — each one is the upstream basin of its "
            "clicked point."
        )
        if st.session_state.last_clicked:
            lat_c, lon_c = st.session_state.last_clicked
            st.write(f"📍 Last clicked: ({lat_c:.4f}, {lon_c:.4f})")
            ws_name = st.text_input(
                "Watershed name (optional)",
                value=f"Watershed {len(st.session_state.watersheds) + 1}",
                key="new_ws_name",
            )
            if st.button("➕ Add watershed at clicked point", use_container_width=True):
                with st.spinner("Computing upstream watershed..."):
                    ws = fetch_watershed_usgs(lon_c, lat_c)
                    if ws:
                        st.session_state.watersheds.append({
                            "name": ws_name or f"Watershed {len(st.session_state.watersheds) + 1}",
                            "geojson": ws,
                            "outlet": (lon_c, lat_c),
                        })
                        st.success(f"Added '{ws_name}'.")
                        st.rerun()
                    else:
                        st.error("Couldn't fetch watershed (US only, near a river).")

        # Show added watersheds with remove buttons
        if st.session_state.watersheds:
            st.markdown("**Added watersheds:**")
            for i, ws in enumerate(st.session_state.watersheds):
                col_a, col_b = st.columns([4, 1])
                with col_a:
                    olat = ws["outlet"][1]
                    olon = ws["outlet"][0]
                    st.write(f"• {ws['name']}  ({olat:.3f}, {olon:.3f})")
                with col_b:
                    if st.button("✕", key=f"rm_ws_{i}", help=f"Remove {ws['name']}"):
                        st.session_state.watersheds.pop(i)
                        st.rerun()
            if st.button("Clear all watersheds", use_container_width=True):
                st.session_state.watersheds = []
                st.rerun()

# ---------------- Main area: tabs ----------------
tab_data, tab_preview, tab_export = st.tabs(["📍 Markers", "🗺️ Preview & adjust", "📥 Export"])

# ---------------- Markers tab ----------------
with tab_data:
    st.subheader("Markers")
    st.caption(
        "Add facilities, sample sites, or anything you want labeled. "
        "Edit cells directly. Use the **Preview** tab to drag markers and labels."
    )

    # Quick add
    with st.expander("➕ Quick add (one at a time)"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1.5])
        with c1:
            new_name = st.text_input("Name", key="new_name")
        with c2:
            new_lat = st.number_input("Lat", value=0.0, format="%.5f", key="new_lat")
        with c3:
            new_lon = st.number_input("Lon", value=0.0, format="%.5f", key="new_lon")
        with c4:
            new_cat = st.selectbox(
                "Category",
                options=list(st.session_state.category_styles.keys()),
                key="new_cat",
            )
        if st.button("Add marker"):
            if new_name:
                new_row = pd.DataFrame([{
                    "name": new_name,
                    "lat": new_lat,
                    "lon": new_lon,
                    "category": new_cat,
                    "label_dx": 0.0,
                    "label_dy": 0.0,
                    "label_pos": "right",
                }])
                st.session_state.markers = pd.concat(
                    [st.session_state.markers, new_row], ignore_index=True
                )
                st.rerun()

    # Bulk paste / CSV
    with st.expander("📋 Paste a table (name, lat, lon, category)"):
        st.caption("Paste from Excel/Google Sheets — first row is header.")
        pasted = st.text_area("Paste here", height=150, key="paste_area")
        if st.button("Add all"):
            try:
                df_new = pd.read_csv(io.StringIO(pasted), sep=None, engine="python")
                # Normalize columns
                df_new.columns = [c.strip().lower() for c in df_new.columns]
                col_map = {
                    "name": "name", "label": "name", "site": "name",
                    "lat": "lat", "latitude": "lat",
                    "lon": "lon", "lng": "lon", "long": "lon", "longitude": "lon",
                    "category": "category", "type": "category",
                }
                df_new = df_new.rename(columns={c: col_map.get(c, c) for c in df_new.columns})
                if "category" not in df_new.columns:
                    df_new["category"] = "Other"
                df_new["label_dx"] = 0.0
                df_new["label_dy"] = 0.0
                df_new["label_pos"] = "right"
                df_new = df_new[["name", "lat", "lon", "category", "label_dx", "label_dy", "label_pos"]]
                st.session_state.markers = pd.concat(
                    [st.session_state.markers, df_new], ignore_index=True
                )
                st.success(f"Added {len(df_new)} markers.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't parse: {e}")

    # Editable table
    st.write("**All markers** (edit any cell, or check the box and click Delete to remove rows)")
    edited = st.data_editor(
        st.session_state.markers,
        num_rows="dynamic",
        column_config={
            "name": st.column_config.TextColumn("Name", width="medium"),
            "lat": st.column_config.NumberColumn("Latitude", format="%.5f"),
            "lon": st.column_config.NumberColumn("Longitude", format="%.5f"),
            "category": st.column_config.SelectboxColumn(
                "Category", options=list(st.session_state.category_styles.keys())
            ),
            "label_dx": st.column_config.NumberColumn("Label x-offset (m)", format="%d"),
            "label_dy": st.column_config.NumberColumn("Label y-offset (m)", format="%d"),
            "label_pos": st.column_config.SelectboxColumn(
                "Label anchor",
                options=["right", "left", "above", "below", "above-right",
                         "above-left", "below-right", "below-left", "centered"],
            ),
        },
        use_container_width=True,
        key="marker_editor",
    )
    st.session_state.markers = edited

    # Category style editor
    with st.expander("🎨 Category styles"):
        for cat in list(st.session_state.category_styles.keys()):
            style = st.session_state.category_styles[cat]
            c1, c2, c3, c4 = st.columns([2, 1.5, 1, 1])
            with c1:
                st.write(f"**{cat}**")
            with c2:
                style["shape"] = st.selectbox(
                    "Shape", ["star", "circle", "square", "triangle", "diamond"],
                    index=["star","circle","square","triangle","diamond"].index(style["shape"]),
                    key=f"shape_{cat}",
                )
            with c3:
                style["color"] = st.color_picker("Color", style["color"], key=f"color_{cat}")
            with c4:
                style["size"] = st.number_input("Size", 50, 1000, style["size"], step=20, key=f"size_{cat}")

# ---------------- Preview tab ----------------
with tab_preview:
    if st.session_state.map_bounds is None:
        st.info("👈 Set a map area in the sidebar first.")
    else:
        st.subheader("Interactive preview")
        st.caption(
            "**Drag markers** to reposition. "
            "**Click on the map** to set a watershed outlet point (then use sidebar). "
            "Changes here update the marker table."
        )

        s, w, n, e = st.session_state.map_bounds
        center = [(s + n) / 2, (w + e) / 2]

        # Build folium map
        m = folium.Map(location=center, tiles=None, zoom_start=11, control_scale=True)
        folium.TileLayer(
            "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            attr="© OpenStreetMap contributors © CARTO",
            name="CARTO Voyager",
        ).add_to(m)
        m.fit_bounds([[s, w], [n, e]])

        # Watersheds (all)
        for ws in st.session_state.watersheds:
            folium.GeoJson(
                ws["geojson"],
                style_function=lambda x: {
                    "color": "black", "weight": 2, "fillOpacity": 0.0
                },
                name=ws["name"],
                tooltip=ws["name"],
            ).add_to(m)

        # Rivers
        if st.session_state.rivers_geojson:
            features_to_plot = st.session_state.rivers_geojson["features"]
            selected = st.session_state.get("selected_rivers", [])
            if selected:
                features_to_plot = [
                    f for f in features_to_plot
                    if f["properties"].get("gnis_name") in selected
                ]
            folium.GeoJson(
                {"type": "FeatureCollection", "features": features_to_plot},
                style_function=lambda x: {"color": "#1f5fa8", "weight": 2.5},
                name="Rivers",
            ).add_to(m)

        # Draggable markers
        for idx, row in st.session_state.markers.iterrows():
            cat_style = st.session_state.category_styles.get(
                row["category"], st.session_state.category_styles["Other"]
            )
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=8,
                color="black",
                weight=1,
                fill=True,
                fillColor=cat_style["color"],
                fillOpacity=1.0,
                popup=f"<b>{row['name']}</b><br>{row['category']}<br>"
                      f"({row['lat']:.4f}, {row['lon']:.4f})",
                tooltip=row["name"],
            ).add_to(m)

        folium.LayerControl().add_to(m)

        map_data = st_folium(m, width=None, height=600, key="folium_preview",
                            returned_objects=["last_clicked"])
        if map_data and map_data.get("last_clicked"):
            st.session_state.last_clicked = (
                map_data["last_clicked"]["lat"],
                map_data["last_clicked"]["lng"],
            )

        st.divider()
        st.subheader("Generate proposal-quality preview")
        if st.button("🖼️ Render high-res preview", type="primary"):
            st.session_state.show_render = True

        if st.session_state.get("show_render"):
            with st.spinner("Rendering map..."):
                try:
                    fig = render_map(
                        bounds=st.session_state.map_bounds,
                        markers=st.session_state.markers,
                        rivers=st.session_state.rivers_geojson,
                        watersheds=st.session_state.watersheds,
                        category_styles=st.session_state.category_styles,
                        selected_rivers=st.session_state.get("selected_rivers", []),
                        show_legend=st.session_state.show_legend,
                        show_scalebar=st.session_state.show_scalebar,
                        show_north_arrow=st.session_state.show_north_arrow,
                        basemap_style=st.session_state.basemap,
                    )
                    st.pyplot(fig, use_container_width=True)
                    plt.close(fig)
                except Exception as ex:
                    st.error(f"Render failed: {ex}")

# ---------------- Render function ----------------
SHAPE_MARKERS = {
    "star": "*", "circle": "o", "square": "s",
    "triangle": "^", "diamond": "D",
}

LABEL_OFFSETS_BY_POS = {
    # multipliers applied to a base offset (in meters)
    "right":       ( 1.0,  0.0, "left",   "center"),
    "left":        (-1.0,  0.0, "right",  "center"),
    "above":       ( 0.0,  1.0, "center", "bottom"),
    "below":       ( 0.0, -1.0, "center", "top"),
    "above-right": ( 0.7,  0.7, "left",   "bottom"),
    "above-left":  (-0.7,  0.7, "right",  "bottom"),
    "below-right": ( 0.7, -0.7, "left",   "top"),
    "below-left":  (-0.7, -0.7, "right",  "top"),
    "centered":    ( 0.0,  0.0, "center", "center"),
}


def render_map(bounds, markers, rivers, watersheds, category_styles,
               selected_rivers, show_legend, show_scalebar, show_north_arrow,
               basemap_style):
    """Render the publication map and return the figure."""
    s, w, n, e = bounds
    WM = "EPSG:3857"

    # Build geodataframes
    if len(markers) == 0:
        raise ValueError("Add at least one marker before rendering.")
    mk = markers.copy().reset_index(drop=True)
    mk_gdf = gpd.GeoDataFrame(
        mk,
        geometry=[Point(lon, lat) for lat, lon in zip(mk["lat"], mk["lon"])],
        crs="EPSG:4326",
    ).to_crs(WM)

    fig, ax = plt.subplots(figsize=(10, 10), dpi=200)

    # Compute extent: union of bounds + marker bbox + 8% padding, square
    bounds_geom = gpd.GeoSeries([box(w, s, e, n)], crs="EPSG:4326").to_crs(WM)
    all_bounds = pd.concat([
        gpd.GeoDataFrame({"geometry": bounds_geom}, crs=WM),
        mk_gdf[["geometry"]],
    ], ignore_index=True)
    minx, miny, maxx, maxy = gpd.GeoDataFrame(all_bounds, crs=WM).total_bounds
    xpad = (maxx - minx) * 0.08
    ypad = (maxy - miny) * 0.08
    xmin, ymin, xmax, ymax = minx - xpad, miny - ypad, maxx + xpad, maxy + ypad
    bw, bh = xmax - xmin, ymax - ymin
    if bw > bh:
        diff = (bw - bh) / 2; ymin -= diff; ymax += diff
    else:
        diff = (bh - bw) / 2; xmin -= diff; xmax += diff
    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)

    # Basemap
    provider_map = {
        "CartoDB.Voyager": cx.providers.CartoDB.Voyager,
        "CartoDB.Positron": cx.providers.CartoDB.Positron,
        "OpenStreetMap": cx.providers.OpenStreetMap.Mapnik,
    }
    cx.add_basemap(ax, source=provider_map.get(basemap_style, cx.providers.CartoDB.Voyager),
                   attribution_size=6)

    # Watersheds (all)
    for ws in (watersheds or []):
        ws_gdf = gpd.GeoDataFrame.from_features(ws["geojson"]["features"], crs="EPSG:4326").to_crs(WM)
        ws_gdf.boundary.plot(ax=ax, color="black", linewidth=1.6, zorder=3)

    # Rivers
    if rivers:
        feats = rivers["features"]
        if selected_rivers:
            feats = [f for f in feats if f["properties"].get("gnis_name") in selected_rivers]
        if feats:
            riv_gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326").to_crs(WM)
            riv_gdf.plot(ax=ax, color="#1f5fa8", linewidth=2.0, zorder=5)

    # Markers grouped by category
    base_offset_m = (xmax - xmin) * 0.018  # 1.8% of map width
    for cat, style in category_styles.items():
        sub = mk_gdf[mk_gdf["category"] == cat]
        if len(sub) == 0:
            continue
        marker_char = SHAPE_MARKERS.get(style["shape"], "o")
        # White halo
        ax.scatter(sub.geometry.x, sub.geometry.y, marker=marker_char,
                   s=style["size"] * 1.3, c="white", edgecolors="white",
                   linewidths=2.5, zorder=8)
        ax.scatter(sub.geometry.x, sub.geometry.y, marker=marker_char,
                   s=style["size"], c=style["color"], edgecolors="black",
                   linewidths=1.0, zorder=9)

    # Labels
    for _, row in mk_gdf.iterrows():
        pos = row.get("label_pos", "right") or "right"
        mx, my, ha, va = LABEL_OFFSETS_BY_POS.get(pos, LABEL_OFFSETS_BY_POS["right"])
        dx_extra = float(row.get("label_dx", 0) or 0)
        dy_extra = float(row.get("label_dy", 0) or 0)
        x = row.geometry.x + mx * base_offset_m + dx_extra
        y = row.geometry.y + my * base_offset_m + dy_extra
        txt = ax.text(x, y, row["name"], fontsize=9, fontweight="bold",
                      color="black", ha=ha, va=va, zorder=10)
        txt.set_path_effects([pe.withStroke(linewidth=3, foreground="white")])

    # Strip axes
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_linewidth(0.8); spine.set_color("#333")

    # Scale bar
    if show_scalebar:
        lat_center = (s + n) / 2
        true_scale = math.cos(math.radians(lat_center))
        scalebar = ScaleBar(true_scale, location="lower right", units="m",
                           length_fraction=0.18, scale_loc="bottom",
                           box_alpha=0.85, box_color="white", color="black",
                           font_properties={"size": 9}, border_pad=0.6, pad=0.4)
        ax.add_artist(scalebar)

    # North arrow
    if show_north_arrow:
        ax_x = xmin + (xmax - xmin) * 0.04
        ax_y = ymax - (ymax - ymin) * 0.08
        ah = (ymax - ymin) * 0.05
        ax.annotate("", xy=(ax_x, ax_y + ah), xytext=(ax_x, ax_y),
                   arrowprops=dict(facecolor="black", edgecolor="black",
                                   width=3.5, headwidth=11, headlength=10),
                   zorder=10)
        ax.text(ax_x, ax_y - (ymax-ymin)*0.012, "N",
               ha="center", va="top", fontsize=11, fontweight="bold", zorder=10)

    # Legend
    if show_legend:
        elements = []
        if watersheds:
            elements.append(Line2D([0], [0], color="black", lw=1.6, label="Watershed boundary"))
        if rivers:
            elements.append(Line2D([0], [0], color="#1f5fa8", lw=2, label="River"))
        for cat, style in category_styles.items():
            if len(mk_gdf[mk_gdf["category"] == cat]) == 0:
                continue
            elements.append(Line2D(
                [0], [0],
                marker=SHAPE_MARKERS.get(style["shape"], "o"),
                color="w", markerfacecolor=style["color"],
                markeredgecolor="black", markersize=12, label=cat, lw=0,
            ))
        if elements:
            leg = ax.legend(handles=elements, loc="lower left",
                          frameon=True, facecolor="white", edgecolor="#666",
                          framealpha=0.92, fontsize=9, title="Legend",
                          title_fontsize=10, borderpad=0.8, labelspacing=0.7)
            leg.get_title().set_fontweight("bold")
            leg.set_zorder(10)

    plt.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    return fig


# ---------------- Export tab ----------------
with tab_export:
    st.subheader("Export options")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.session_state.show_legend = st.checkbox("Show legend", value=st.session_state.show_legend)
    with c2:
        st.session_state.show_scalebar = st.checkbox("Show scale bar", value=st.session_state.show_scalebar)
    with c3:
        st.session_state.show_north_arrow = st.checkbox("Show north arrow", value=st.session_state.show_north_arrow)

    st.session_state.basemap = st.selectbox(
        "Basemap style",
        ["CartoDB.Voyager", "CartoDB.Positron", "OpenStreetMap"],
        index=["CartoDB.Voyager", "CartoDB.Positron", "OpenStreetMap"].index(st.session_state.basemap),
    )

    dpi = st.slider("Export DPI (higher = larger file, sharper image)", 150, 600, 300, step=50)

    if st.button("📥 Generate PNG", type="primary"):
        if st.session_state.map_bounds is None:
            st.error("Set a map area first.")
        elif len(st.session_state.markers) == 0:
            st.error("Add at least one marker first.")
        else:
            with st.spinner("Rendering high-resolution map..."):
                try:
                    fig = render_map(
                        bounds=st.session_state.map_bounds,
                        markers=st.session_state.markers,
                        rivers=st.session_state.rivers_geojson,
                        watersheds=st.session_state.watersheds,
                        category_styles=st.session_state.category_styles,
                        selected_rivers=st.session_state.get("selected_rivers", []),
                        show_legend=st.session_state.show_legend,
                        show_scalebar=st.session_state.show_scalebar,
                        show_north_arrow=st.session_state.show_north_arrow,
                        basemap_style=st.session_state.basemap,
                    )
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                               facecolor="white")
                    plt.close(fig)
                    buf.seek(0)
                    st.success("Map ready!")
                    st.download_button(
                        "⬇️ Download PNG",
                        data=buf,
                        file_name="proposal_map.png",
                        mime="image/png",
                        use_container_width=True,
                    )
                    st.image(buf, use_container_width=True)
                except Exception as ex:
                    st.error(f"Export failed: {ex}")

    st.divider()
    st.subheader("Save / load project")
    st.caption("Download all your work as a JSON file you can load later.")

    if st.button("💾 Save project to JSON"):
        project = {
            "map_bounds": st.session_state.map_bounds,
            "markers": st.session_state.markers.to_dict(orient="records"),
            "rivers_geojson": st.session_state.rivers_geojson,
            "watersheds": st.session_state.watersheds,
            "selected_rivers": st.session_state.get("selected_rivers", []),
            "category_styles": st.session_state.category_styles,
        }
        st.download_button(
            "⬇️ Download project.json",
            data=json.dumps(project, indent=2),
            file_name="map_project.json",
            mime="application/json",
        )

    uploaded = st.file_uploader("📂 Load project from JSON", type=["json"])
    if uploaded:
        try:
            project = json.load(uploaded)
            st.session_state.map_bounds = tuple(project["map_bounds"])
            st.session_state.markers = pd.DataFrame(project["markers"])
            st.session_state.rivers_geojson = project.get("rivers_geojson")
            # Backwards compatibility: old projects had a single watershed_geojson
            if "watersheds" in project:
                st.session_state.watersheds = project["watersheds"]
            elif project.get("watershed_geojson"):
                st.session_state.watersheds = [{
                    "name": "Watershed 1",
                    "geojson": project["watershed_geojson"],
                    "outlet": (0.0, 0.0),
                }]
            else:
                st.session_state.watersheds = []
            st.session_state.selected_rivers = project.get("selected_rivers", [])
            if "category_styles" in project:
                st.session_state.category_styles = project["category_styles"]
            st.success("Project loaded! Switch to the Preview tab.")
        except Exception as ex:
            st.error(f"Couldn't load: {ex}")
