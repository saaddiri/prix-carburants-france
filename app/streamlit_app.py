"""Carte interactive des prix des carburants.

Lancer depuis la racine du projet :  streamlit run app\\streamlit_app.py
"""
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from fuel.mapdata import color_range, day_bounds, day_prices, fuels, with_distance
from fuel.nearby import find_commune

DB = os.environ.get("FUEL_DB", "data/analytics/fuel.duckdb")
# Bleu (pas cher) -> jaune clair (moyen) -> rouge foncé (cher)
COLORS = ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c", "#7f0000"]

st.set_page_config(page_title="Prix des carburants", layout="wide")


@st.cache_resource
def get_con():
    return duckdb.connect(DB, read_only=True)


@st.cache_data(show_spinner=False)
def load_day(day: date, fuel: str, show_outliers: bool):
    return day_prices(get_con(), day, fuel, show_outliers)


con = get_con()
first_day, last_day = day_bounds(con)

st.title("Observatoire des prix des carburants")
st.caption("Source : prix-carburants.gouv.fr (Licence Ouverte 2.0). France métropolitaine + Corse.")

with st.sidebar:
    fuel = st.selectbox("Carburant", fuels(con), index=0)
    day = st.date_input("Jour", value=last_day, min_value=first_day, max_value=last_day)
    show_outliers = st.checkbox("Afficher les prix incohérents", value=False)
    query = st.text_input("Commune", placeholder="ex. Lyon")
    radius = st.slider("Rayon (km)", 1, 50, 10)
    st.caption("Un prix est « valable » s'il a au plus 30 jours. Échelle de couleurs : "
               "du 2e au 98e percentile des prix du jour.")

df, masked = load_day(day, fuel, show_outliers)
if df.empty:
    st.warning("Aucun prix pour ce jour et ce carburant.")
    st.stop()

center, zoom, best = {"lat": 46.6, "lon": 2.5}, 4.6, None
if query.strip():
    found = find_commune(con, query.strip())
    if not found:
        st.sidebar.error("Commune introuvable.")
    else:
        labels = [f"{c} ({d}) - {n} stations" for c, d, n, *_ in found]
        pick = found[labels.index(st.sidebar.selectbox("Précise", labels))] if len(found) > 1 else found[0]
        center, zoom = {"lat": pick[3], "lon": pick[4]}, 10.5
        near = with_distance(df, pick[3], pick[4])
        best = near[near["distance_km"] <= radius].nsmallest(10, ["price", "distance_km"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Stations", f"{len(df):,}".replace(",", " "))
c2.metric("Médiane", f"{df['price'].median():.3f} €/L")
c3.metric("Moins chère (hors incohérents)" if not show_outliers else "Moins chère", f"{df['price'].min():.3f} €/L")
c4.metric("Plus chère (hors incohérents)" if not show_outliers else "Plus chère", f"{df['price'].max():.3f} €/L")
if masked and not show_outliers:
    st.caption(f"{masked} station(s) masquée(s) : prix incohérent avec la médiane du jour.")

lo, hi = color_range(df["price"])
df = df.assign(
    maj=df["price_updated_at"].dt.strftime("%d/%m/%Y %H:%M"),
    age=df["age_days"].astype(int),
)
fig = px.scatter_map(
    df, lat="latitude", lon="longitude", color="price", range_color=(lo, hi),
    color_continuous_scale=COLORS, center=center, zoom=zoom, height=650,
    hover_name="city", hover_data={"address": True, "price": ":.3f", "maj": True, "age": True,
                                   "latitude": False, "longitude": False},
    labels={"price": "€/L", "maj": "Prix mis à jour le", "age": "Âge du prix (jours)", "address": "Adresse"},
)
fig.update_traces(marker={"size": 7})
if best is not None and not best.empty:
    fig.add_trace(go.Scattermap(
        lat=best["latitude"], lon=best["longitude"], mode="markers", name="Les moins chères",
        marker={"size": 15, "color": "black"}, hoverinfo="skip",
    ))
fig.update_layout(map_style="open-street-map", margin={"l": 0, "r": 0, "t": 0, "b": 0},
                  coloraxis_colorbar={"title": "€/L"}, showlegend=False)
st.plotly_chart(fig, width="stretch")

if best is not None:
    st.subheader(f"Les 10 stations les moins chères à moins de {radius} km")
    if best.empty:
        st.info("Aucune station dans ce rayon.")
    else:
        table = best[["price", "distance_km", "city", "address", "age_days"]].rename(columns={
            "price": "Prix €/L", "distance_km": "Distance km", "city": "Commune",
            "address": "Adresse", "age_days": "Âge du prix (j)"})
        st.dataframe(table.round({"Distance km": 1}), hide_index=True, width="stretch")