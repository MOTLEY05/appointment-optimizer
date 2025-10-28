#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import streamlit as st
import pandas as pd
import requests
import os
from datetime import timedelta

# ======================================
# 🔐 CONFIGURATION
# ======================================
LOOKER_BASE_URL = "https://weinfuse.cloud.looker.com/api/4.0"
CLIENT_ID = st.secrets.get("LOOKER_CLIENT_ID", "43JnKGJSRJSmd42CfP6B")
CLIENT_SECRET = st.secrets.get("LOOKER_CLIENT_SECRET", "X4JRgWYxsbrY7cstW34dRjnD")
LOOK_ENDPOINT = "https://weinfuse.cloud.looker.com/api/4.0/looks/8792/run/json?limit=-1"
CLINIC_MINUTES = 540  # daily capacity per chair

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

def get_appointment_data():
    """Fetch appointment data from Looker Look endpoint."""
    token = get_looker_token()
    headers = {"Authorization": f"token {token}"}
    r = requests.get(LOOK_ENDPOINT, headers=headers)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    st.success(f"✅ Retrieved {len(df)} appointment records from Looker.")
    return df

# ======================================
# 🧮 OPTIMIZATION / UTILIZATION LOGIC
# ======================================
def preprocess(df):
    """Filter appointments and calculate durations."""
    df = df[df["appointments.status"].isin(["Complete", "Active"])].copy()
    df["appointments.start_time"] = pd.to_datetime(df["appointments.start_time"])
    df["appointments.end_time"] = pd.to_datetime(df["appointments.end_time"])
    df["appointments.created_date"] = pd.to_datetime(df["appointments.created_date"]).dt.date

    df["Original_Date"] = df["appointments.start_time"].dt.date
    df["Duration"] = (df["appointments.end_time"] - df["appointments.start_time"]).dt.total_seconds() / 60
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
    """Compute chair-level daily utilization metrics."""
    util = (
        df.groupby(["locations.name", "appointments.chair_id", "Original_Date"])
        .agg(Total_Minutes=("Duration", "sum"), Appointments=("Duration", "count"))
        .reset_index()
    )
    util["Available_Minutes"] = CLINIC_MINUTES
    util["Remaining_Minutes"] = util["Available_Minutes"] - util["Total_Minutes"]
    util["Utilization_%"] = (util["Total_Minutes"] / CLINIC_MINUTES * 100).round(1)
    return util

def get_optimal_times(df, location, chair_id, duration):
    """Return top 3 least-utilized days for the given location and chair."""
    util = calculate_utilization(df)
    chair_util = util[
        (util["locations.name"] == location)
        & (util["appointments.chair_id"] == chair_id)
    ]
    if chair_util.empty:
        st.warning(f"No data found for {location} (Chair {chair_id}).")
        return pd.DataFrame()

    chair_util["Can_Accommodate"] = chair_util["Remaining_Minutes"] >= duration
    available = chair_util[chair_util["Can_Accommodate"]].copy()
    if available.empty:
        st.warning(f"No available capacity for {location} (Chair {chair_id}).")
        return pd.DataFrame()

    ranked = available.sort_values(
        by=["Remaining_Minutes", "Original_Date"], ascending=[False, True]
    )
    return ranked.head(3)[["Original_Date", "Remaining_Minutes", "Utilization_%"]]

# ======================================
# 🖥️ STREAMLIT UI
# ======================================
st.set_page_config(page_title="Appointment Optimizer", layout="centered")
st.title("💊 Appointment Optimization Tool")
st.write("This app retrieves appointment data from Looker and shows the most open days by location and chair.")

if st.button("🔄 Refresh Data from Looker"):
    with st.spinner("Loading data..."):
        try:
            df_raw = get_appointment_data()
            st.session_state["data"] = preprocess(df_raw)
        except Exception as e:
            st.error(f"Error connecting to Looker: {e}")

if "data" in st.session_state:
    df = st.session_state["data"]
    st.subheader("📍 Choose Parameters")

    location = st.selectbox("Select Location", sorted(df["locations.name"].unique()))
    chair_list = sorted(df[df["locations.name"] == location]["appointments.chair_id"].unique())
    chair_id = st.selectbox("Select Chair", chair_list)
    duration = st.number_input("Appointment Duration (minutes)", min_value=1, max_value=540, value=60)

    if st.button("📅 Find Optimal Days"):
        results = get_optimal_times(df, location, chair_id, duration)
        if not results.empty:
            st.subheader(f"Top 3 Days for {location} (Chair {chair_id})")
            st.dataframe(results, use_container_width=True)
else:
    st.info("Click **Refresh Data from Looker** to start.")

st.markdown("---")
st.caption("Powered by Looker API | Streamlit Appointment Optimization © 2025")

