#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import streamlit as st
import pandas as pd
import requests
import os
from datetime import timedelta, date
import holidays
from PIL import Image

# ======================================
# 🔐 CONFIGURATION
# ======================================
LOOKER_BASE_URL = "https://weinfuse.cloud.looker.com/api/4.0"
CLIENT_ID = st.secrets.get("LOOKER_CLIENT_ID", "43JnKGJSRJSmd42CfP6B")
CLIENT_SECRET = st.secrets.get("LOOKER_CLIENT_SECRET", "X4JRgWYxsbrY7cstW34dRjnD")
LOOK_ID = 8792  # Looker Look ID
CLINIC_MINUTES = 540
us_holidays = holidays.US()  # U.S. federal holidays

# ======================================
# 🖼️ ADD LOGO (Local File Path)
# ======================================
# Local logo path (change if moved)
LOGO_PATH = "Vivo.png"

try:
    logo = Image.open(LOGO_PATH)
    st.image(logo, width=180)
except Exception as e:
    st.warning(f"⚠️ Unable to load logo from {LOGO_PATH}: {e}")

st.markdown("## Appointment Optimization Tool")
st.write(
    "This app connects to Looker and finds the most available future appointment days "
    "for each Vivo Infusion location, excluding U.S. holidays."
)

# ======================================
# 🔌 LOOKER API FUNCTIONS
# ======================================
def get_looker_token():
    """Authenticate with Looker and return an access token."""
    url = f"{LOOKER_BASE_URL}/login"
    payload = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    r = requests.post(url, data=payload)
    r.raise_for_status()
    return r.json()["access_token"]

def get_locations_list():
    """Fetch unique locations quickly from Looker."""
    token = get_looker_token()
    headers = {"Authorization": f"token {token}"}
    url = f"{LOOKER_BASE_URL}/looks/{LOOK_ID}/run/json?fields=locations.name&limit=-1"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    return sorted(df["locations.name"].dropna().unique())

def get_appointment_data(location_name):
    """Pull appointment data for a specific location only."""
    token = get_looker_token()
    headers = {"Authorization": f"token {token}"}
    url = f"{LOOKER_BASE_URL}/looks/{LOOK_ID}/run/json?limit=-1&filter=locations.name:{location_name}"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    st.success(f"✅ Retrieved {len(df)} appointment records for {location_name}.")
    return df

# ======================================
# 🧮 DATA PREPARATION + OPTIMIZATION
# ======================================
def preprocess(df):
    """Filter and clean appointment data."""
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date
    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60

    # Only future appointments
    today = date.today()
    df = df[df["Original_Date"] >= today]

    # Exclude U.S. holidays
    df = df[~df["Original_Date"].isin(us_holidays)]

    return df[
        [
            "locations.name",
            "appointments.chair_id",
            "administration_details.med_name",
            "Duration",
            "Original_Date",
        ]
    ].dropna()

def calculate_utilization(df):
    """Calculate chair-level utilization for each date."""
    util = (
        df.groupby(["locations.name", "Original_Date"])
        .agg(Total_Minutes=("Duration", "sum"), Appointments=("Duration", "count"))
        .reset_index()
    )
    util["Available_Minutes"] = CLINIC_MINUTES
    util["Remaining_Minutes"] = util["Available_Minutes"] - util["Total_Minutes"]
    util["Utilization_%"] = (util["Total_Minutes"] / CLINIC_MINUTES * 100).round(1)
    return util

def get_optimal_times(df, location, duration):
    """Return top 3 least-utilized days for the given location."""
    util = calculate_utilization(df)
    loc_util = util[util["locations.name"] == location]
    if loc_util.empty:
        st.warning(f"No appointment data found for {location}.")
        return pd.DataFrame()

    loc_util["Can_Accommodate"] = loc_util["Remaining_Minutes"] >= duration
    available = loc_util[loc_util["Can_Accommodate"]].copy()
    if available.empty:
        st.warning(f"No open capacity found for {location}.")
        return pd.DataFrame()

    ranked = available.sort_values(
        by=["Remaining_Minutes", "Original_Date"], ascending=[False, True]
    )
    return ranked.head(3)[["Original_Date", "Remaining_Minutes", "Utilization_%"]]

# ======================================
# 🖥️ STREAMLIT INTERFACE
# ======================================
st.set_page_config(page_title="Vivo Appointment Optimizer", layout="centered")

if "locations" not in st.session_state:
    with st.spinner("Loading available locations from Looker..."):
        try:
            st.session_state["locations"] = get_locations_list()
        except Exception as e:
            st.error(f"Error loading locations: {e}")

if "locations" in st.session_state:
    location = st.selectbox("📍 Select Location", st.session_state["locations"])

    if st.button("🔄 Load Schedule for Selected Location"):
        with st.spinner(f"Loading data for {location}..."):
            try:
                df_raw = get_appointment_data(location)
                st.session_state["data"] = preprocess(df_raw)
            except Exception as e:
                st.error(f"Error retrieving schedule for {location}: {e}")

if "data" in st.session_state:
    df = st.session_state["data"]
    duration = st.number_input(
        "Appointment Duration (minutes)", min_value=1, max_value=540, value=60
    )

    if st.button("📅 Find Optimal Appointment Days"):
        results = get_optimal_times(df, location, duration)
        if not results.empty:
            st.subheader(f"Top 3 Most Available Future Days — {location}")
            st.dataframe(results, use_container_width=True)
else:
    st.info("Select a location and click **Load Schedule** to begin.")

st.markdown("---")
st.caption("Vivo Infusion | Appointment Optimization via Looker API © 2025")

