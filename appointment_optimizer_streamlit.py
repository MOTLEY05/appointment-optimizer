#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import streamlit as st
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, date, time
import holidays
from PIL import Image

# ======================================
# 🔐 CONFIGURATION
# ======================================
LOOKER_BASE_URL = "https://weinfuse.cloud.looker.com/api/4.0"
CLIENT_ID = st.secrets.get("LOOKER_CLIENT_ID", "43JnKGJSRJSmd42CfP6B")
CLIENT_SECRET = st.secrets.get("LOOKER_CLIENT_SECRET", "X4JRgWYxsbrY7cstW34dRjnD")
LOOK_ID = 8792  # Looker Look ID
CLINIC_HOURS = 9  # hours per day
MINUTES_PER_HOUR = 60
CLINIC_START = time(8, 0)
us_holidays = holidays.US()

# ======================================
# 🖼️ LOGO
# ======================================
LOGO_PATH = "Vivo.png"
try:
    logo = Image.open(LOGO_PATH)
    st.image(logo, width=180)
except Exception as e:
    st.warning(f"⚠️ Unable to load logo from {LOGO_PATH}: {e}")

st.markdown("## Appointment Optimization Tool")
st.write(
    "This app connects to Looker and finds the next three earliest appointment times "
    "for a selected Vivo Infusion clinic location. Total capacity is calculated as "
    "the number of chairs at the clinic × 9 hours. U.S. holidays are excluded."
)

# ======================================
# 🔌 LOOKER API FUNCTIONS
# ======================================
def get_looker_token():
    url = f"{LOOKER_BASE_URL}/login"
    payload = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    r = requests.post(url, data=payload)
    r.raise_for_status()
    return r.json()["access_token"]

def get_locations_list():
    """Fetch a quick list of all unique locations."""
    token = get_looker_token()
    headers = {"Authorization": f"token {token}"}
    url = f"{LOOKER_BASE_URL}/looks/{LOOK_ID}/run/json?fields=locations.name&limit=-1"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    return sorted(df["locations.name"].dropna().unique())

def get_appointment_data(location_name):
    """Fetch appointment data from Looker for a specific location."""
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

    # Filter for future appointments
    today = date.today()
    df = df[df["Original_Date"] >= today]

    # Exclude U.S. holidays
    df = df[~df["Original_Date"].isin(us_holidays)]

    return df[
        ["locations.name", "appointments.chair_id", "administration_details.med_name", "Duration", "Original_Date"]
    ].dropna()

def calculate_capacity(df):
    """Calculate total capacity per clinic (chairs × 9 hours × 60 minutes)."""
    chair_counts = df.groupby("locations.name")["appointments.chair_id"].nunique().to_dict()
    capacity_map = {loc: chairs * CLINIC_HOURS * MINUTES_PER_HOUR for loc, chairs in chair_counts.items()}
    return capacity_map

def calculate_utilization(df):
    """Aggregate daily utilization per location and scale by total chairs."""
    util = (
        df.groupby(["locations.name", "Original_Date"])
        .agg(Total_Minutes=("Duration", "sum"))
        .reset_index()
    )
    capacity_map = calculate_capacity(df)
    util["Available_Minutes"] = util["locations.name"].map(capacity_map)
    util["Remaining_Minutes"] = util["Available_Minutes"] - util["Total_Minutes"]
    util["Remaining_Minutes"] = util["Remaining_Minutes"].clip(lower=0)
    return util

def get_optimal_times(df, location, duration):
    """Find next 3 earliest appointment start times without exceeding clinic capacity."""
    util = calculate_utilization(df)
    today = datetime.now().date()

    loc_util = util[
        (util["locations.name"] == location) & (util["Original_Date"] >= today)
    ].copy()

    # Exclude holidays
    loc_util = loc_util[~loc_util["Original_Date"].isin(us_holidays)]

    # Only include days with remaining room
    loc_util = loc_util[loc_util["Remaining_Minutes"] >= duration]

    if loc_util.empty:
        st.warning(f"No available time slots remaining for {location}.")
        return pd.DataFrame()

    # Compute next available appointment time for each day
    loc_util["Next_Start_Minute"] = loc_util["Available_Minutes"] - loc_util["Remaining_Minutes"]
    loc_util["Next_Available_Time"] = [
        (datetime.combine(row["Original_Date"], CLINIC_START) + timedelta(minutes=row["Next_Start_Minute"])).time()
        for _, row in loc_util.iterrows()
    ]

    # Sort and return top 3
    loc_util = loc_util.sort_values(by=["Original_Date", "Next_Available_Time"])
    return loc_util.head(3)[["Original_Date", "Next_Available_Time", "Remaining_Minutes"]]

# ======================================
# 🖥️ STREAMLIT INTERFACE
# ======================================
st.set_page_config(page_title="Vivo Appointment Optimizer", layout="centered")

if "locations" not in st.session_state:
    with st.spinner("Loading available locations from Looker..."):
        try:
            st.session_state["locations"] = get_locations_list()
        except Exception as e:
            st.error(f"Error retrieving locations: {e}")

if "locations" in st.session_state:
    location = st.selectbox("📍 Select Location", st.session_state["locations"])

    if st.button("🔄 Load Schedule for Selected Location"):
        with st.spinner(f"Loading appointment data for {location}..."):
            try:
                df_raw = get_appointment_data(location)
                st.session_state["data"] = preprocess(df_raw)
            except Exception as e:
                st.error(f"Error retrieving appointment data for {location}: {e}")

if "data" in st.session_state:
    df = st.session_state["data"]
    duration = st.number_input("Appointment Duration (minutes)", min_value=1, max_value=540, value=60)

    if st.button("📅 Find Next 3 Optimal Appointment Times"):
        results = get_optimal_times(df, location, duration)
        if not results.empty:
            st.subheader(f"Next 3 Optimal Appointment Times — {location}")
            st.dataframe(results, use_container_width=True)
else:
    st.info("Select a location and click **Load Schedule** to begin.")

st.markdown("---")
st.caption("Vivo Infusion | Appointment Optimization via Looker API © 2025")

