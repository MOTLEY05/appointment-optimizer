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
OPTIMIZATION_WINDOW_DAYS = 30
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
    f"This app connects to Looker and optimizes appointments **within the next {OPTIMIZATION_WINDOW_DAYS} days**. "
    "Total daily capacity = number of chairs × 9 hours (8 AM–5 PM). "
    "All results exclude U.S. holidays and export a single `Rebalanced_Assigned` worksheet."
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
# 🧮 DATA PREPARATION + CAPACITY
# ======================================
def preprocess(df):
    """Clean and filter appointments within 30 days of today."""
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date
    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60

    today = date.today()
    cutoff = today + timedelta(days=OPTIMIZATION_WINDOW_DAYS)
    # ✅ Include appointments between today and 30 days from now
    df = df[(df["Original_Date"] >= today) & (df["Original_Date"] <= cutoff)]
    df = df[~df["Original_Date"].isin(us_holidays)]

    df["Appt_ID"] = df.index
    return df[
        ["Appt_ID", "locations.name", "appointments.chair_id", "administration_details.med_name", "Duration", "Original_Date"]
    ].dropna()

def calculate_capacity(df):
    """Clinic capacity = #chairs × 540 minutes."""
    chair_counts = df.groupby("locations.name")["appointments.chair_id"].nunique().to_dict()
    return {loc: chairs * CLINIC_HOURS * MINUTES_PER_HOUR for loc, chairs in chair_counts.items()}

def calculate_utilization(df):
    util = (
        df.groupby(["locations.name", "Original_Date"])
        .agg(Total_Minutes=("Duration", "sum"))
        .reset_index()
    )
    cap = calculate_capacity(df)
    util["Available_Minutes"] = util["locations.name"].map(cap)
    util["Remaining_Minutes"] = util["Available_Minutes"] - util["Total_Minutes"]
    util["Remaining_Minutes"] = util["Remaining_Minutes"].clip(lower=0)
    return util

# ======================================
# 🧠 MULTI-DAY OPTIMIZATION LOGIC
# ======================================
def multi_day_optimize(df, location, duration):
    """Distribute appointments across top 3 optimal days (within 30 days)."""
    util = calculate_utilization(df)
    today = datetime.now().date()
    cutoff = today + timedelta(days=OPTIMIZATION_WINDOW_DAYS)

    loc_util = util[
        (util["locations.name"] == location)
        & (util["Original_Date"] >= today)
        & (util["Original_Date"] <= cutoff)
    ].copy()
    loc_util = loc_util[~loc_util["Original_Date"].isin(us_holidays)]
    if loc_util.empty:
        st.warning(f"No available capacity for {location} within 30 days.")
        return pd.DataFrame()

    # Rank top 3 days with most open time
    loc_util = loc_util.sort_values("Remaining_Minutes", ascending=False).head(3)
    day_caps = dict(zip(loc_util["Original_Date"], loc_util["Remaining_Minutes"]))

    rebalanced = df.copy()
    rebalanced["Assigned_Date"] = None

    # Assign appointments sequentially to the top 3 optimal days
    for idx, row in rebalanced.iterrows():
        for day in day_caps.keys():
            if day_caps[day] >= row["Duration"]:
                rebalanced.at[idx, "Assigned_Date"] = day
                day_caps[day] -= row["Duration"]
                break

    # Fallback: assign remaining to the most available day
    rebalanced["Assigned_Date"] = rebalanced["Assigned_Date"].fillna(list(day_caps.keys())[-1])

    rebalanced["Assigned_Date"] = pd.to_datetime(rebalanced["Assigned_Date"])
    rebalanced["Original_Date"] = pd.to_datetime(rebalanced["Original_Date"])
    rebalanced["Days_Moved"] = (rebalanced["Assigned_Date"] - rebalanced["Original_Date"]).dt.days
    rebalanced["Created_At"] = datetime.today().date()

    return rebalanced

# ======================================
# 📤 EXCEL EXPORT (REBALANCED_ASSIGNED)
# ======================================
def export_rebalanced_assigned(rebalanced, location):
    """Write only Rebalanced_Assigned sheet."""
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
        with st.spinner(f"Fetching data for {location}..."):
            try:
                df_raw = get_appointment_data(location)
                st.session_state["data"] = preprocess(df_raw)
            except Exception as e:
                st.error(f"Error retrieving appointment data for {location}: {e}")

if "data" in st.session_state:
    df = st.session_state["data"]
    duration = st.number_input("Appointment Duration (minutes)", min_value=1, max_value=540, value=60)

    if st.button("📅 Optimize Next 3 Future Days (≤30 Days)"):
        rebalanced_df = multi_day_optimize(df, location, duration)
        if not rebalanced_df.empty:
            st.subheader(f"Rebalanced Assignments — {location} (Within 30 Days)")
            st.dataframe(rebalanced_df, use_container_width=True)
            export_rebalanced_assigned(rebalanced_df, location)
else:
    st.info("Select a location and click **Load Schedule** to begin.")

st.markdown("---")
st.caption("Vivo Infusion | Appointment Optimization via Looker API © 2025")

