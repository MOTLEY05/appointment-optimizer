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
from io import BytesIO
import openpyxl

# ======================================
# 🔐 CONFIGURATION
# ======================================
LOOKER_BASE_URL = "https://weinfuse.cloud.looker.com/api/4.0"
CLIENT_ID = st.secrets.get("LOOKER_CLIENT_ID", "43JnKGJSRJSmd42CfP6B")
CLIENT_SECRET = st.secrets.get("LOOKER_CLIENT_SECRET", "X4JRgWYxsbrY7cstW34dRjnD")
LOOK_ID = 8792
CLINIC_HOURS = 9  # 8 AM–5 PM
MINUTES_PER_HOUR = 60
CLINIC_START = time(8, 0)
CLINIC_END = time(17, 0)
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
    "for a selected Vivo Infusion clinic location. Total daily capacity = number of chairs × 9 hours (8 AM–5 PM). "
    "All results exclude U.S. holidays."
)

# ======================================
# 🔌 LOOKER API
# ======================================
def get_looker_token():
    url = f"{LOOKER_BASE_URL}/login"
    payload = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    r = requests.post(url, data=payload)
    r.raise_for_status()
    return r.json()["access_token"]

def get_locations_list():
    token = get_looker_token()
    headers = {"Authorization": f"token {token}"}
    url = f"{LOOKER_BASE_URL}/looks/{LOOK_ID}/run/json?fields=locations.name&limit=-1"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    return sorted(df["locations.name"].dropna().unique())

def get_appointment_data(location_name):
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
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date

    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60

    today = date.today()
    df = df[df["Original_Date"] >= today]
    df = df[~df["Original_Date"].isin(us_holidays)]

    return df[
        ["locations.name", "appointments.chair_id", "administration_details.med_name", "Duration", "Original_Date"]
    ].dropna()

def calculate_capacity(df):
    """Total clinic capacity per day = number of chairs × 9 hours × 60 minutes."""
    chair_counts = df.groupby("locations.name")["appointments.chair_id"].nunique().to_dict()
    return {loc: chairs * CLINIC_HOURS * MINUTES_PER_HOUR for loc, chairs in chair_counts.items()}

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
    """Find next 3 earliest appointment start times within 8 AM–5 PM hours."""
    util = calculate_utilization(df)
    today = datetime.now().date()

    loc_util = util[
        (util["locations.name"] == location) & (util["Original_Date"] >= today)
    ].copy()
    loc_util = loc_util[~loc_util["Original_Date"].isin(us_holidays)]
    loc_util = loc_util[loc_util["Remaining_Minutes"] >= duration]

    if loc_util.empty:
        st.warning(f"No available time slots remaining for {location}.")
        return pd.DataFrame()

    # Calculate utilization ratio relative to capacity
    loc_util["Utilization_Ratio"] = loc_util["Total_Minutes"] / loc_util["Available_Minutes"]
    loc_util["Utilization_Ratio"] = loc_util["Utilization_Ratio"].clip(upper=1)

    total_day_minutes = CLINIC_HOURS * 60  # 540 minutes per day
    loc_util["Next_Start_Minute"] = loc_util["Utilization_Ratio"] * total_day_minutes

    def compute_next_available(row):
        next_time = datetime.combine(row["Original_Date"], CLINIC_START) + timedelta(minutes=row["Next_Start_Minute"])
        # If next time exceeds 5 PM, set to 5 PM
        if next_time.time() > CLINIC_END:
            next_time = datetime.combine(row["Original_Date"], CLINIC_END)
        return next_time.strftime("%I:%M %p")

    loc_util["Next_Available_Time"] = loc_util.apply(compute_next_available, axis=1)
    loc_util = loc_util.dropna(subset=["Next_Available_Time"])

    loc_util = loc_util.sort_values(by=["Original_Date", "Next_Available_Time"])
    return loc_util.head(3)[["Original_Date", "Next_Available_Time", "Remaining_Minutes"]]

# ======================================
# 📤 EXCEL EXPORT
# ======================================
def export_optimized_schedule(df, location):
    """Generate Excel output file with detailed schedule and utilization summary."""
    util = calculate_utilization(df)
    capacity_map = calculate_capacity(df)
    chair_counts = df.groupby("locations.name")["appointments.chair_id"].nunique().to_dict()

    summary = (
        util.groupby(["locations.name", "Original_Date"])
        .agg(Total_Minutes=("Total_Minutes", "sum"))
        .reset_index()
    )
    summary["Total_Chairs"] = summary["locations.name"].map(chair_counts)
    summary["Available_Minutes"] = summary["locations.name"].map(capacity_map)
    summary["Utilization_%"] = (summary["Total_Minutes"] / summary["Available_Minutes"] * 100).round(1)

    # Convert to Excel in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        util.to_excel(writer, index=False, sheet_name="Daily_Utilization")
        summary.to_excel(writer, index=False, sheet_name="Clinic_Summary")

    output.seek(0)
    st.download_button(
        label="⬇️ Download Optimized Schedule",
        data=output,
        file_name=f"Optimized_Schedule_With_ChairUtilization_{location}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

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

            # ✅ Add Excel export button
            export_optimized_schedule(df, location)
else:
    st.info("Select a location and click **Load Schedule** to begin.")

st.markdown("---")
st.caption("Vivo Infusion | Appointment Optimization via Looker API © 2025")

