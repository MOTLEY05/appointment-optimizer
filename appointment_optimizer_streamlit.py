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
CLINIC_MINUTES = 540  # 9 hours * 60 minutes
CLINIC_START = time(8, 0)  # 8:00 AM clinic start
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
    "for a selected Vivo Infusion clinic location. The schedule is optimized to not exceed "
    "the total available clinic minutes per day and excludes U.S. holidays."
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
    # Apply filter dynamically to fetch data only for the selected location
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
    """Clean, filter, and prepare appointment data for optimization."""
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date

    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60

    # Filter for future appointments only
    today = date.today()
    df = df[df["Original_Date"] >= today]

    # Exclude U.S. holidays
    df = df[~df["Original_Date"].isin(us_holidays)]

    return df[
        ["locations.name", "appointments.chair_id", "administration_details.med_name", "Duration", "Original_Date"]
    ].dropna()

def calculate_utilization(df):
    """Aggregate daily utilization per location."""
    util = (
        df.groupby(["locations.name", "Original_Date"])
        .agg(Total_Minutes=("Duration", "sum"))
        .reset_index()
    )
    util["Available_Minutes"] = CLINIC_MINUTES
    util["Remaining_Minutes"] = util["Available_Minutes"] - util["Total_Minutes"]
    util["Remaining_Minutes"] = util["Remaining_Minutes"].clip(lower=0)
    return util

def get_optimal_times(df, location, duration):
    """Find the next 3 earliest appointment start times that fit daily capacity."""
    util = calculate_utilization(df)
    today = datetime.now().date()

    loc_util = util[
        (util["locations.name"] == location) & (util["Original_Date"] >= today)
    ].copy()

    # Exclude holidays again just in case
    loc_util = loc_util[~loc_util["Original_Date"].isin(us_holidays)]

    # Only days with enough remaining capacity
    loc_util = loc_util[loc_util["Remaining_Minutes"] >= duration]

    if loc_util.empty:
        st.warning(f"No available time slots remaining for {location}.")
        return pd.DataFrame()

    # Compute estimated next available start time
    loc_util["Next_Start_Minute"] = CLINIC_MINUTES - loc_util["Remaining_Minutes"]
    loc_util["Next_Available_Time"] = [
        (datetime.combine(row["Original_Date"], CLINIC_START) + timedelta(minutes=row["Next_Start_Minute"])).time()
        for _, row in loc_util.iterrows()
    ]

    # Sort by date and earliest time
    loc_util = loc_util.sort_values(by=["Original_Date", "Next_Available_Time"])
    return loc_util.head(3)[["Original_Date", "Next_Available_Time", "Remaining_Minutes"]]

# ======================================
# 🖥️ STREAMLIT INTERFACE
# ======================================
st.set_page_config(page_title="Vivo Appointment Optimizer", layout="centered")

# Step 1 — Fetch locations
if "locations" not in st.session_state:
    with st.spinner("Loading available locations from Looker..."):
        try:
            st.session_state["locations"] = get_locations_list()
        except Exception as e:
            st.error(f"Error retrieving locations: {e}")

# Step 2 — Select location and load its schedule
if "locations" in st.session_state:
    location = st.selectbox("📍 Select Location", st.session_state["locations"])

    if st.button("🔄 Load Schedule for Selected Location"):
        with st.spinner(f"Loading appointment data for {location}..."):
            try:
                df_raw = get_appointment_data(location)
                st.session_state["data"] = preprocess(df_raw)
            except Exception as e:
                st.error(f"Error retrieving appointment data for {location}: {e}")

# Step 3 — Run optimization once data is loaded
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

