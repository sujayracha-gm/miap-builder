# River & Site Mapper — deploy guide

A simple web app for building proposal-quality maps. This guide walks you through deploying it to Streamlit Cloud (free) so anyone can use it from a browser.

## What you'll end up with

A URL like `https://your-app-name.streamlit.app` that opens a fully-featured map builder. No installs, no terminal — just bookmark it and use it from any browser.

## What you need

1. A GitHub account (free) — https://github.com/signup
2. A Streamlit Cloud account (free) — https://streamlit.io/cloud
3. About 10 minutes

## Step-by-step deploy

### 1. Create a new GitHub repository

- Go to https://github.com/new
- Name it something like `river-site-mapper`
- Set it to **Public** (required for free Streamlit Cloud)
- Check "Add a README file"
- Click "Create repository"

### 2. Upload the two files

In your new repo, click **Add file → Upload files**, then drag in:
- `app.py`
- `requirements.txt`

Click **Commit changes** at the bottom.

### 3. Deploy on Streamlit Cloud

- Go to https://share.streamlit.io
- Sign in with your GitHub account
- Click **New app**
- Select:
  - **Repository:** the repo you just made
  - **Branch:** `main`
  - **Main file path:** `app.py`
- Click **Deploy**

Wait 2–3 minutes while Streamlit installs the dependencies. When it's done, you'll see your map builder running at a URL like `https://river-site-mapper-xxxx.streamlit.app`.

That's it. Bookmark the URL and share it with anyone.

## How to use the app

### Workflow

1. **Sidebar → Map area** — search a city/address, or paste `lat, lon` (e.g. `34.05, -118.24`).
2. **Sidebar → Add layers** — click "Get rivers (US)" for USGS data, or "Get rivers (worldwide)" for OpenStreetMap.
3. **Markers tab** — add markers via the quick-add form, by pasting a table from Excel, or by editing the table directly.
4. **Preview tab** — see the interactive map. Click any point on the map to set a watershed outlet (US only — fetch via sidebar). Then click "Render high-res preview" to see the proposal-quality version.
5. **Export tab** — toggle legend / scale bar / north arrow, choose DPI (300 is good for documents, 600 for print), and download the PNG.

### Tips for label placement

In the markers table:
- **Label anchor** dropdown picks the side (right, above, below-left, etc.)
- **Label x-offset** and **y-offset** are in *meters* — type a positive number to push right/up, negative to push left/down. This is how to nudge labels to avoid overlaps.

### Save your work

The Export tab has **Save project to JSON** — download a single file containing all your markers, rivers, watershed, and styles. To pick up where you left off, use **Load project from JSON**.

## Updating the app

If you want to change anything in the code:
1. Edit `app.py` in your GitHub repo (click the file → pencil icon)
2. Commit changes
3. Streamlit Cloud auto-redeploys in ~1 minute

## Troubleshooting

**"Get rivers" button does nothing or errors out**
- Try a smaller map area. The USGS API has limits on bounding box size.
- Switch from USGS to OSM (or vice versa) — they cover different regions.

**Watershed fetch fails**
- USGS watersheds are US-only. Outside the US, the watershed feature won't work.
- Try clicking closer to a known river — the API needs a point near a stream.

**Labels overlap**
- Adjust the **label_dx** / **label_dy** columns in the marker table (in meters), or change the **label_pos** anchor.

**Map area is too big / too small**
- Re-search with a more specific location. For example, "Pasadena, CA" gives a tighter area than "California".

## Costs

Free. Streamlit Cloud's free tier supports up to 1GB of memory per app, which is plenty for this. The app uses public APIs (USGS, OpenStreetMap, CARTO) that are also free.

## Privacy note

Streamlit Cloud apps deployed from public repos are publicly accessible at their URL. Anyone with the link can use it. If you need it private, that requires a paid Streamlit Cloud tier or self-hosting elsewhere.
