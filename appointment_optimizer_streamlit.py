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
OPTIMIZATION_WINDOW_DAYS = 30  # rolling window
us_holidays = holidays.US()

# ======================================
# 🖼️ LOGO
# ======================================
LOGO_PATH = r"C:\Users\Kyle Motley\Pictures\Vivo.png"
try:
    logo = Image.open(LOGO_PATH)
    st.image(logo, width=180)
except Exception as e:
    st.warning(f"⚠️ Unable to load logo from {LOGO_PATH}: {e}")

st.markdown("## Appointment Optimization Tool")
st.write(
    f"This app connects to Looker and optimizes appointments only **{OPTIMIZATION_WINDOW_DAYS} days or more** into the future. "
    "Total daily capacity = number of chairs × 9 hours (8 AM–5 PM). All results exclude U.S. holidays and export a single `Rebalanced_Assigned` worksheet."
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
    """Clean Looker data and keep only appointments 30+ days in the future."""
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date
    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60
    future_cutoff = date.today() + timedelta(days=OPTIMIZATION_WINDOW_DAYS)
    df = df[df["Original_Date"] >= future_cutoff]
    df = df[~df["Original_Date"].isin(us_holidays)]
    df["Appt_ID"] = df.index
    return df[
        ["Appt_ID", "locations.name", "appointments.chair_id", "administration_details.med_name", "Duration", "Original_Date"]
    ].dropna()

def calculate_capacity(df):
    chair_counts = df.groupby("locations.name")["appointments.chair_id"].nunique().to_dict()
    return {loc: chairs * CLINIC_HOURS * MINUTES_PER_HOUR for loc, chairs in chair_counts.items()}

def calculate_utilization(df):
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
    """Optimize only for appointments 30+ days in the future."""
    util = calculate_utilization(df)
    today = datetime.now().date()
    future_cutoff = today + timedelta(days=OPTIMIZATION_WINDOW_DAYS)

    loc_util = util[
        (util["locations.name"] == location) & (util["Original_Date"] >= future_cutoff)
    ].copy()
    loc_util = loc_util[~loc_util["Original_Date"].isin(us_holidays)]
    loc_util = loc_util[loc_util["Remaining_Minutes"] >= duration]

    if loc_util.empty:
        st.warning(f"No available appointment slots 30+ days ahead for {location}.")
        return pd.DataFrame()

    loc_util["Utilization_Ratio"] = loc_util["Total_Minutes"] / loc_util["Available_Minutes"]
    loc_util["Utilization_Ratio"] = loc_util["Utilization_Ratio"].clip(upper=1)
    total_day_minutes = CLINIC_HOURS * 60
    loc_util["Next_Start_Minute"] = loc_util["Utilization_Ratio"] * total_day_minutes

    def compute_next_available(row):
        next_time = datetime.combine(row["Original_Date"], CLINIC_START) + timedelta(minutes=row["Next_Start_Minute"])
        if next_time.time() > CLINIC_END:
            next_time = datetime.combine(row["Original_Date"], CLINIC_END)
        return next_time.strftime("%I:%M %p")

    loc_util["Next_Available_Time"] = loc_util.apply(compute_next_available, axis=1)
    loc_util = loc_util.sort_values(by=["Original_Date", "Next_Available_Time"])
    loc_util["Assigned_DateTime"] = loc_util.apply(
        lambda x: datetime.combine(x["Original_Date"], datetime.strptime(x["Next_Available_Time"], "%I:%M %p").time()),
        axis=1
    )
    return loc_util.head(3)[
        ["Original_Date", "Next_Available_Time", "Remaining_Minutes", "Assigned_DateTime"]
    ]

# ======================================
# 📤 EXCEL EXPORT (REBALANCED ASSIGNED ONLY)
# ======================================
def export_rebalanced_assigned(df, optimized_df, location):
    """Export results in Rebalanced_Assigned format only."""
    rebalanced = df.copy()

    if optimized_df.empty:
        st.warning("⚠️ No optimized appointments found to export.")
        return

    rebalanced["Assigned_Date"] = optimized_df.iloc[0]["Original_Date"]
    rebalanced["Next_Available_Time"] = optimized_df.iloc[0]["Next_Available_Time"]

    # Convert to datetime for subtraction
    rebalanced["Assigned_Date"] = pd.to_datetime(rebalanced["Assigned_Date"])
    rebalanced["Original_Date"] = pd.to_datetime(rebalanced["Original_Date"])
    rebalanced["Days_Moved"] = (rebalanced["Assigned_Date"] - rebalanced["Original_Date"]).dt.days
    rebalanced["Created_At"] = datetime.today().date()

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        rebalanced.to_excel(writer, index=False, sheet_name="Rebalanced_Assigned")

    output.seek(0)
    st.download_button(
        label="⬇️ Download Rebalanced_Assigned Excel",
        data=output,
        file_name=f"Optimized_Schedule_Rebalanced_{location}.xlsx",
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
            st.subheader(f"Next 3 Optimal Appointment Times — {location} (30+ Days Ahead)")
            st.dataframe(results, use_container_width=True)
            export_rebalanced_assigned(df, results, location)
else:
    st.info("Select a location and click **Load Schedule** to begin.")

st.markdown("---")
st.caption("Vivo Infusion | Appointment Optimization via Looker API © 2025")

