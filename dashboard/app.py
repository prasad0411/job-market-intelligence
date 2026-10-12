#!/usr/bin/env python3
"""
Job Hunt Analytics Dashboard
    streamlit run dashboard/app.py
"""

import streamlit as st
import pandas as pd
import gspread
import os
import re
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(
    page_title="Job Hunt Analytics | Prasad Kanade",
    page_icon="--",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    .stApp { background-color: #0f1116; color: #e8eaed; }
    * { font-family: 'Inter', sans-serif !important; }
    h1,h2,h3 { color: #e8eaed !important; font-weight: 700 !important; }
    .main-title { font-size: 40px; font-weight: 800; color: #e8eaed; margin-bottom: 2px; margin-top: 0px; letter-spacing: -0.5px; }
    .main-sub { font-size: 20px; color: #7a8290; margin-bottom: 24px; }
    .metric-card { background: linear-gradient(145deg, #181c24 0%, #141720 100%); border: 1px solid #252a35; border-radius: 14px; padding: 20px 14px; text-align: center; height: 155px; display: flex; flex-direction: column; justify-content: center; }
    .metric-value { font-size: 36px; font-weight: 800; margin: 6px 0; letter-spacing: -1px; }
    .metric-label { font-size: 13px; color: #7a8290; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600; }
    .metric-sub { font-size: 13px; color: #6b7380; margin-top: 4px; }
    .v-blue{color:#5b9bf5} .v-green{color:#4ade80} .v-red{color:#f87171} .v-amber{color:#fbbf24} .v-purple{color:#a78bfa} .v-cyan{color:#22d3ee} .v-white{color:#e8eaed} .v-orange{color:#fb923c}
    .section-title { font-size: 22px; font-weight: 700; color: #c0c6d0; margin-top: 40px; margin-bottom: 18px; padding-bottom: 10px; border-bottom: 2px solid #1e2330; }
    #MainMenu{visibility:hidden} footer{visibility:hidden} header{visibility:hidden}
    .block-container{padding-top:1rem !important}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300)
def load_sheets():
    creds_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".local",
        "credentials.json",
    )
    if os.path.exists(creds_path):
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            creds_path,
            [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ],
        )
    else:
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(
                dict(st.secrets["gcp_service_account"]),
                [
                    "https://spreadsheets.google.com/feeds",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
        except Exception:
            st.error("No credentials found.")
            return {}
    gc = gspread.authorize(creds)
    ss = gc.open("H1B visa")
    sheets = {}
    for tab in [
        "Internship Applications",
        "Valid Entries",
        "Discarded Entries",
        "Outreach Tracker",
        "Reviewed - Not Applied",
    ]:
        try:
            ws = ss.worksheet(tab)
            all_vals = ws.get_all_values()
            if len(all_vals) > 1:
                headers = all_vals[0]
                seen = {}
                clean_headers = []
                for h in headers:
                    h = h.strip()
                    if not h:
                        h = f"_empty_{len(clean_headers)}"
                    if h in seen:
                        seen[h] += 1
                        h = f"{h}_{seen[h]}"
                    else:
                        seen[h] = 0
                    clean_headers.append(h)
                sheets[tab] = pd.DataFrame(all_vals[1:], columns=clean_headers)
            else:
                sheets[tab] = pd.DataFrame()
        except Exception:
            sheets[tab] = pd.DataFrame()
    return sheets


def parse_date(d):
    if not d or not isinstance(d, str) or d.strip() in ("", "N/A"):
        return None
    d = d.strip()
    for fmt in [
        "%d %B, %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%B %d %Y",
        "%d %B, %I:%M %p",
        "%d %B, %I:%M:%S %p",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(d, fmt)
            return dt.replace(year=2026) if dt.year < 2000 else dt
        except ValueError:
            continue
    m = re.match(r"(\w+ \d{1,2},?\s*\d{4})", d)
    if m:
        try:
            return datetime.strptime(m.group(1).replace(",", ""), "%B %d %Y")
        except ValueError:
            pass
    return None


def norm_status(s):
    if not s or not isinstance(s, str):
        return "Unknown"
    sl = s.strip().lower()
    if "offer" in sl:
        return "Offer"
    if "interview" in sl:
        return "Interview"
    if "oa round 2" in sl:
        return "OA Round 2"
    if "oa round 1" in sl:
        return "OA Round 1"
    if "assessment" in sl:
        return "Assessment"
    if "not applied" in sl:
        return "Not Applied"
    if "rejected" in sl:
        return "Rejected"
    if "applied" in sl:
        return "Applied"
    return s.strip()


def norm_source(s):
    if not s or not isinstance(s, str) or not s.strip():
        return "Unknown"
    sl = s.strip()
    mapping = {
        "SimplifyJobs": "Simplify Repo",
        "vanshb03": "Vansh Repo",
        "SWE List": "SWE List",
        "SWE List Email": "SWE List",
        "Jobright": "Jobright",
        "Jobright/LinkedIn": "Jobright",
        "ZipRecruiter": "ZipRecruiter",
        "Manual": "Manual",
        "LinkedIn": "LinkedIn",
        "GitHub": "GitHub",
    }
    return mapping.get(sl, sl)


def card(label, value, color="v-blue", sub=""):
    s = f'<div class="metric-sub">{sub}</div>' if sub else ""
    return f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value {color}">{value}</div>{s}</div>'


def combine(sheets):
    frames = []
    for name in ["Internship Applications", "Valid Entries"]:
        df = sheets.get(name, pd.DataFrame())
        if df.empty:
            continue
        col_map = {}
        for c in df.columns:
            cl = c.strip().lower()
            if cl == "status":
                col_map[c] = "status"
            elif cl == "company":
                col_map[c] = "company"
            elif cl in ("title", "job title"):
                col_map[c] = "title"
            elif cl == "date applied":
                col_map[c] = "date_applied"
            elif cl == "location":
                col_map[c] = "location"
            elif cl in ("remote?", "remote"):
                col_map[c] = "remote"
            elif cl == "source":
                col_map[c] = "source"
            elif cl == "notes":
                col_map[c] = "notes"
        df = df.rename(columns=col_map)
        df["_src"] = name
        keep = [
            c
            for c in [
                "status",
                "company",
                "title",
                "date_applied",
                "location",
                "remote",
                "source",
                "notes",
                "_src",
            ]
            if c in df.columns
        ]
        frames.append(df[keep])
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # Remove rows with no company
    if "company" in df.columns:
        df = df[df["company"].apply(lambda x: bool(str(x).strip()))]
    df["status_clean"] = (
        df["status"].apply(norm_status) if "status" in df.columns else "Unknown"
    )
    if "source" in df.columns:
        df["source_clean"] = df["source"].apply(norm_source)
    if "date_applied" in df.columns:
        df["dt"] = df["date_applied"].apply(parse_date)
        df["date_only"] = df["dt"].apply(
            lambda d: d.date() if d is not None and not pd.isna(d) else None
        )
        df["month"] = df["dt"].apply(
            lambda d: d.strftime("%Y-%m") if d is not None and not pd.isna(d) else None
        )
        df["month_label"] = df["dt"].apply(
            lambda d: d.strftime("%b %Y") if d is not None and not pd.isna(d) else None
        )
    return df


def main():
    st.markdown(
        '<div class="main-title">Job Hunt Analytics</div>', unsafe_allow_html=True
    )

    with st.spinner("Loading..."):
        sheets = load_sheets()
    if not sheets:
        return

    # Pre-compute outreach stats for banner
    _out = sheets.get("Outreach Tracker", pd.DataFrame())
    emails_sent = 0
    if not _out.empty:
        for _col in _out.columns:
            _cl = _col.strip().lower()
            if "hm email" in _cl or "recruiter email" in _cl:
                emails_sent += (
                    _out[_col]
                    .apply(lambda x: bool(str(x).strip() and "@" in str(x)))
                    .sum()
                )

    df = combine(sheets)
    disc = sheets.get("Discarded Entries", pd.DataFrame())
    out = sheets.get("Outreach Tracker", pd.DataFrame())
    rev = sheets.get("Reviewed - Not Applied", pd.DataFrame())

    if df.empty:
        st.warning("No data found.")
        return

    # Filter future dates
    if "dt" in df.columns:
        now = datetime.now()
        df = df[
            df["dt"].apply(
                lambda d: d is None or pd.isna(d) or d <= now + timedelta(days=1)
            )
        ]

    total = len(df)
    sc = df["status_clean"].value_counts()
    applied = sc.get("Applied", 0)
    rejected = sc.get("Rejected", 0)
    oa1 = sc.get("OA Round 1", 0) + sc.get("Assessment", 0)
    oa2 = sc.get("OA Round 2", 0)
    interviews = sc.get("Interview", 0)
    # Count OA Round 2 as interviews too (Datacor went to technical interview via OA Round 2)
    total_interviews = interviews + oa2
    offers = sc.get("Offer", 0)
    not_applied = sc.get("Not Applied", 0)
    disc_total = len(disc) if not disc.empty else 0
    rev_total = len(rev) if not rev.empty else 0

    # ── Banner
    months_active = "7"
    if "date_only" in df.columns:
        dates = df["date_only"].dropna()
        if len(dates) > 0:
            first = min(dates)
            last = max(dates)
            months_active = str(
                max(1, (last.year - first.year) * 12 + last.month - first.month)
            )
    unique_companies = df["company"].nunique() if "company" in df.columns else 0

    try:
        from zoneinfo import ZoneInfo

        _et_now = datetime.now(ZoneInfo("US/Eastern"))
    except Exception:
        _et_now = datetime.now()
    st.markdown(
        f'<div style="text-align:right;color:#555d6b;font-size:15px;margin-top:-10px;margin-bottom:10px;">Last updated: {_et_now.strftime("%b %d, %Y at %I:%M %p")} Eastern Time Zone.</div>',
        unsafe_allow_html=True,
    )

    # ── Pipeline ───────────────────────────────────────────────
    st.markdown(
        '<div class="section-title">Application Pipeline</div>', unsafe_allow_html=True
    )
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    with c1:
        st.markdown(
            card("Total Applications", total, "v-white"), unsafe_allow_html=True
        )
    with c2:
        st.markdown(
            card(
                "Applied", applied, "v-green", f"{applied*100//max(total,1)}% of total"
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(card("Not Applied", not_applied, "v-blue"), unsafe_allow_html=True)
    with c4:
        st.markdown(
            card(
                "Rejected",
                rejected,
                "v-red",
                f"{rejected*100//max(applied+rejected,1)}% rate",
            ),
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            card(
                "OA / Assessment",
                oa1,
                "v-amber",
                f"R1: {sc.get('OA Round 1',0) + sc.get('Assessment',0)}",
            ),
            unsafe_allow_html=True,
        )
    with c6:
        st.markdown(
            card(
                "Interviews",
                total_interviews,
                "v-purple",
                f"R1: {interviews} | R2: {oa2}",
            ),
            unsafe_allow_html=True,
        )
    with c7:
        st.markdown(card("Offers", offers, "v-cyan"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Automation Metrics ─────────────────────────────────────
    st.markdown(
        '<div class="section-title">Automation Metrics</div>', unsafe_allow_html=True
    )

    # Outreach stats from actual sheet
    emails_sent = 0
    emails_delivered = 0
    emails_bounced = 0
    companies_reached = 0
    li_msgs_generated = 0

    if not out.empty:
        for col in out.columns:
            cl = col.strip().lower()
            if "hm email" in cl:
                hm_count = (
                    out[col]
                    .apply(lambda x: bool(str(x).strip() and "@" in str(x)))
                    .sum()
                )
                emails_sent += hm_count
                companies_reached = hm_count
            elif "recruiter email" in cl:
                emails_sent += (
                    out[col]
                    .apply(lambda x: bool(str(x).strip() and "@" in str(x)))
                    .sum()
                )
            elif cl == "notes":
                emails_delivered += (
                    out[col].apply(lambda x: "Delivered" in str(x)).sum()
                )
                emails_bounced += (
                    out[col].apply(lambda x: "bounced" in str(x).lower()).sum()
                )
            elif "linkedin msg" in cl:
                li_msgs_generated += (
                    out[col]
                    .apply(lambda x: bool(str(x).strip() and len(str(x).strip()) > 20))
                    .sum()
                )

    # Count rows with Send At filled (emails actually scheduled/sent)
    emails_scheduled = 0
    if not out.empty:
        for col in out.columns:
            if "send at" in col.strip().lower():
                emails_scheduled = out[col].apply(lambda x: bool(str(x).strip())).sum()
                break

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        st.markdown(
            card(
                "Jobs Processed",
                total + disc_total,
                "v-white",
                f"{total} valid | {disc_total} filtered",
            ),
            unsafe_allow_html=True,
        )
    with a2:
        st.markdown(
            card("Reviewed and Skipped", rev_total, "v-blue"), unsafe_allow_html=True
        )
    with a3:
        st.markdown(
            card(
                "Outreach Emails Sent",
                emails_sent,
                "v-green",
                f"{emails_scheduled} scheduled",
            ),
            unsafe_allow_html=True,
        )
    with a4:
        st.markdown(
            card(
                "Emails Bounced",
                emails_bounced,
                "v-red",
                f"{emails_delivered} delivered",
            ),
            unsafe_allow_html=True,
        )
    with a5:
        st.markdown(
            card(
                "Companies Reached",
                companies_reached,
                "v-purple",
                f"{li_msgs_generated} LinkedIn msgs",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Status + Source ────────────────────────────────────────
    st.markdown(
        '<div class="section-title">Application Breakdown</div>', unsafe_allow_html=True
    )

    colors = {
        "Applied": "#4ade80",
        "Not Applied": "#475569",
        "Rejected": "#f87171",
        "OA Round 1": "#fbbf24",
        "OA Round 2": "#fb923c",
        "Assessment": "#f59e0b",
        "Interview": "#a78bfa",
        "Offer": "#22d3ee",
        "Unknown": "#334155",
    }

    ch1, ch2 = st.columns(2)
    with ch1:
        scd = df["status_clean"].value_counts().reset_index()
        scd.columns = ["Status", "Count"]
        fig = px.pie(
            scd,
            values="Count",
            names="Status",
            color="Status",
            color_discrete_map=colors,
            hole=0.55,
        )
        fig.update_layout(
            title=dict(text="Status Distribution", font=dict(size=18, color="#c0c6d0")),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#7a8290", size=13),
            margin=dict(t=50, b=20, l=20, r=20),
            height=420,
        )
        fig.update_traces(textinfo="value+percent", textfont_size=13)
        st.plotly_chart(fig, use_container_width=True)

    with ch2:
        if "source_clean" in df.columns:
            src = df["source_clean"].value_counts().head(10).reset_index()
            src.columns = ["Source", "Count"]
            fig2 = px.bar(
                src,
                x="Count",
                y="Source",
                orientation="h",
                color="Count",
                color_continuous_scale=["#1e3a5f", "#5b9bf5"],
            )
            fig2.update_layout(
                title=dict(
                    text="Applications by Source", font=dict(size=18, color="#c0c6d0")
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=13),
                xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(gridcolor="#1c2230", autorange="reversed"),
                coloraxis_showscale=False,
                margin=dict(t=50, b=20, l=20, r=20),
                height=420,
            )
            fig2.update_traces(
                texttemplate="%{x}", textposition="outside", textfont_size=12
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ── Timeline + Monthly ─────────────────────────────────────
    st.markdown(
        '<div class="section-title">Application Timeline</div>', unsafe_allow_html=True
    )

    ch3, ch4 = st.columns(2)

    with ch3:
        if "date_only" in df.columns:
            # Weekly aggregation for wider bars
            df_dated = df.dropna(subset=["date_only"]).copy()
            df_dated["date_dt"] = pd.to_datetime(df_dated["date_only"])
            df_dated = df_dated[df_dated["date_dt"] <= pd.Timestamp.now()]
            weekly = (
                df_dated.set_index("date_dt")
                .resample("W")["status_clean"]
                .count()
                .reset_index()
            )
            weekly.columns = ["Week", "Count"]
            weekly["Cumulative"] = weekly["Count"].cumsum()

            fig3 = go.Figure()
            fig3.add_trace(
                go.Bar(
                    x=weekly["Week"],
                    y=weekly["Count"],
                    name="Weekly",
                    marker_color="#1e3a5f",
                    opacity=0.8,
                    width=5 * 86400000,  # 5 days wide
                )
            )
            fig3.add_trace(
                go.Scatter(
                    x=weekly["Week"],
                    y=weekly["Cumulative"],
                    name="Cumulative",
                    line=dict(color="#5b9bf5", width=3),
                    yaxis="y2",
                )
            )
            fig3.update_layout(
                title=dict(
                    text="Applications Over Time (Weekly)",
                    font=dict(size=18, color="#c0c6d0"),
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=13),
                xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(title="Weekly", gridcolor="#1c2230"),
                yaxis2=dict(
                    title="Cumulative",
                    overlaying="y",
                    side="right",
                    gridcolor="#1c2230",
                ),
                legend=dict(x=0.01, y=0.99, font=dict(size=12)),
                margin=dict(t=50, b=20, l=20, r=40),
                height=420,
                hovermode="x unified",
            )
            st.plotly_chart(fig3, use_container_width=True)

    with ch4:
        if "month" in df.columns:
            monthly = (
                df.dropna(subset=["month"])
                .groupby(["month", "month_label"])
                .size()
                .reset_index()
            )
            monthly.columns = ["sort", "Month", "Apps"]
            monthly = monthly.sort_values("sort")
            current_month = datetime.now().strftime("%Y-%m")
            monthly = monthly[monthly["sort"] <= current_month]

            fig4 = px.bar(
                monthly,
                x="Month",
                y="Apps",
                color="Apps",
                color_continuous_scale=["#1e3a5f", "#5b9bf5", "#93c5fd"],
                text="Apps",
            )
            fig4.update_layout(
                title=dict(
                    text="Applications by Month", font=dict(size=18, color="#c0c6d0")
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=13),
                xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(gridcolor="#1c2230"),
                coloraxis_showscale=False,
                margin=dict(t=80, b=20, l=20, r=20),
                height=480,
            )
            fig4.update_traces(
                textposition="outside", textfont_size=14, textfont_color="#c0c6d0"
            )
            st.plotly_chart(fig4, use_container_width=True)

    # ── Companies + Location ───────────────────────────────────
    st.markdown(
        '<div class="section-title">Companies and Locations</div>',
        unsafe_allow_html=True,
    )

    ch5, ch6 = st.columns(2)

    with ch5:
        if "company" in df.columns:
            top = df["company"].value_counts().head(15).reset_index()
            top.columns = ["Company", "Roles"]
            fig5 = px.bar(
                top,
                x="Roles",
                y="Company",
                orientation="h",
                color="Roles",
                color_continuous_scale=["#1e3a5f", "#5b9bf5"],
                text="Roles",
            )
            fig5.update_layout(
                title=dict(
                    text="Most Applied Companies", font=dict(size=18, color="#c0c6d0")
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=13),
                xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(gridcolor="#1c2230", autorange="reversed"),
                coloraxis_showscale=False,
                margin=dict(t=50, b=20, l=20, r=20),
                height=450,
            )
            fig5.update_traces(
                textposition="outside", textfont_size=12, textfont_color="#c0c6d0"
            )
            st.plotly_chart(fig5, use_container_width=True)

    with ch6:
        if "location" in df.columns:

            def get_state(loc):
                if (
                    not loc
                    or not isinstance(loc, str)
                    or loc.strip() in ("", "Unknown", "N/A")
                ):
                    return "Unknown"
                ll = loc.lower().strip()
                if "remote" in ll:
                    return "Remote"
                if "hybrid" in ll:
                    return "Hybrid"
                m = re.search(r",\s*([A-Z]{2})\b", str(loc))
                if m:
                    return m.group(1)
                state_names = {"california":"CA","new york":"NY","texas":"TX","massachusetts":"MA","illinois":"IL","washington":"WA","virginia":"VA","florida":"FL","georgia":"GA","colorado":"CO","pennsylvania":"PA","ohio":"OH","north carolina":"NC","michigan":"MI","minnesota":"MN","oregon":"OR","maryland":"MD","new jersey":"NJ","connecticut":"CT","indiana":"IN","arizona":"AZ","tennessee":"TN","missouri":"MO","wisconsin":"WI","utah":"UT","iowa":"IA","kansas":"KS","nebraska":"NE","kentucky":"KY","alabama":"AL","south carolina":"SC","louisiana":"LA","oklahoma":"OK","nevada":"NV","new mexico":"NM","idaho":"ID","delaware":"DE","rhode island":"RI","new hampshire":"NH","maine":"ME","district of columbia":"DC"}
                for name, abbr in state_names.items():
                    if name in ll:
                        return abbr
                return "Unknown"

            df["state"] = df["location"].apply(get_state)
            lc = df["state"].value_counts().head(12).reset_index()
            lc.columns = ["Location", "Count"]
            fig6 = px.bar(
                lc,
                x="Count",
                y="Location",
                orientation="h",
                color="Count",
                color_continuous_scale=["#1e3a5f", "#22d3ee"],
                text="Count",
            )
            fig6.update_layout(
                title=dict(
                    text="Applications by State", font=dict(size=18, color="#c0c6d0")
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=13),
                xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(gridcolor="#1c2230", autorange="reversed"),
                coloraxis_showscale=False,
                margin=dict(t=50, b=20, l=20, r=20),
                height=450,
            )
            fig6.update_traces(
                textposition="outside", textfont_size=12, textfont_color="#c0c6d0"
            )
            st.plotly_chart(fig6, use_container_width=True)

    # ── Conversion Funnel ──────────────────────────────────────
    st.markdown(
        '<div class="section-title">Conversion Funnel</div>', unsafe_allow_html=True
    )

    total_applied = applied + rejected + oa1 + oa2 + interviews + offers
    stages = [
        ("Jobs Found", total + disc_total + rev_total),
        ("Passed Filters", total + rev_total),
        ("Applied", total_applied),
        ("OA / Assessment", oa1 + oa2 + interviews + offers),
        ("Interviews", total_interviews + offers),
        ("Offers", offers),
    ]
    stages = [(s, v) for s, v in stages if v > 0]

    if stages:
        fig_f = go.Figure(
            go.Funnel(
                y=[s[0] for s in stages],
                x=[s[1] for s in stages],
                textinfo="value+percent initial",
                marker=dict(
                    color=[
                        "#334155",
                        "#475569",
                        "#4ade80",
                        "#fbbf24",
                        "#a78bfa",
                        "#22d3ee",
                    ][: len(stages)]
                ),
                connector=dict(line=dict(color="#252a35")),
            )
        )
        fig_f.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c0c6d0", size=14),
            margin=dict(t=20, b=20, l=20, r=20),
            height=350,
        )
        st.plotly_chart(fig_f, use_container_width=True)

    # ── Work Mode ──────────────────────────────────────────────
    if "remote" in df.columns:
        st.markdown(
            '<div class="section-title">Work Mode</div>', unsafe_allow_html=True
        )

        def norm_remote(r):
            if not r or not isinstance(r, str):
                return "Unknown"
            rl = r.strip().lower()
            if "remote" in rl:
                return "Remote"
            if "hybrid" in rl:
                return "Hybrid"
            if any(x in rl for x in ["on site", "onsite", "on-site", "in person"]):
                return "On Site"
            return "Unknown"

        df["remote_clean"] = df["remote"].apply(norm_remote)
        rc = df["remote_clean"].value_counts().reset_index()
        rc.columns = ["Mode", "Count"]
        remote_colors = {
            "Remote": "#22d3ee",
            "Hybrid": "#a78bfa",
            "On Site": "#4ade80",
            "Unknown": "#334155",
        }

        fig_r = px.pie(
            rc,
            values="Count",
            names="Mode",
            color="Mode",
            color_discrete_map=remote_colors,
            hole=0.5,
        )
        fig_r.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#7a8290", size=13),
            margin=dict(t=20, b=20, l=20, r=20),
            height=350,
        )
        fig_r.update_traces(textinfo="value+percent", textfont_size=13)
        _, mid, _ = st.columns([1, 2, 1])
        with mid:
            st.plotly_chart(fig_r, use_container_width=True)




def brain_tab():
    """Pipeline Intelligence tab — shows everything Brain has learned."""
    import json, os, datetime
    st.markdown('<div class="section-title">🧠 Pipeline Intelligence</div>', unsafe_allow_html=True)

    brain_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".local", "brain.json")
    if not os.path.exists(brain_path):
        st.warning("Brain not initialized yet. Run the outreach pipeline first.")
        return

    try:
        with open(brain_path) as f:
            b = json.load(f)
    except Exception as e:
        st.error(f"Failed to load Brain: {e}")
        return

    # ── Top metrics ─────────────────────────────────────────────────────────
    patterns = b.get("patterns", {})
    total_att = patterns.get("total_attempts", 0)
    total_suc = patterns.get("total_successes", 0)
    domains = b.get("domains", {})
    companies = b.get("companies", {})
    mx_cache = b.get("mx_cache", {})
    linkedin_names = b.get("linkedin_names", {})
    job_ids = b.get("job_id_registry", {})
    simplify_q = b.get("simplify_retry_queue", {})
    cb = b.get("circuit_breaker", {})
    rh = b.get("run_history", {})

    acc = round(total_suc / total_att * 100, 1) if total_att else 0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.markdown(card("Domains Learned", len(domains), "v-blue"), unsafe_allow_html=True)
    with m2:
        st.markdown(card("Pattern Accuracy", f"{acc}%", "v-green" if acc >= 80 else "v-amber"), unsafe_allow_html=True)
    with m3:
        st.markdown(card("MX Records Cached", len(mx_cache), "v-cyan"), unsafe_allow_html=True)
    with m4:
        st.markdown(card("Job IDs Registered", len(job_ids), "v-purple"), unsafe_allow_html=True)
    with m5:
        st.markdown(card("LinkedIn Names", len(linkedin_names), "v-orange"), unsafe_allow_html=True)
    with m6:
        st.markdown(card("Companies Tracked", len(companies), "v-white"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    # ── Pattern success rates ────────────────────────────────────────────────
    with col1:
        st.markdown('<div class="section-title" style="font-size:18px;margin-top:10px;">Email Pattern Rankings</div>', unsafe_allow_html=True)
        gr = patterns.get("global_success_rates", {})
        if gr:
            import plotly.graph_objects as go
            sorted_pats = sorted(gr.items(), key=lambda x: x[1], reverse=True)
            pats = [p[0] for p in sorted_pats]
            rates = [round(p[1] * 100, 1) for p in sorted_pats]
            colors = ["#4ade80" if r >= 50 else "#fbbf24" if r >= 20 else "#f87171" for r in rates]
            fig = go.Figure(go.Bar(
                x=rates, y=pats, orientation="h",
                marker_color=colors,
                text=[f"{r}%" for r in rates],
                textposition="outside",
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290", size=12),
                xaxis=dict(gridcolor="#1c2230", title="Success Rate %"),
                yaxis=dict(gridcolor="#1c2230"),
                margin=dict(t=10, b=20, l=20, r=60), height=300,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No pattern data yet — run outreach pipeline")

    # ── Source quality ────────────────────────────────────────────────────────
    with col2:
        st.markdown('<div class="section-title" style="font-size:18px;margin-top:10px;">Source Quality</div>', unsafe_allow_html=True)
        sq = b.get("aggregator", {}).get("source_quality", {})
        if sq:
            import plotly.express as px
            sq_data = [{"Source": k, "Quality %": round(v.get("quality_rate", 0) * 100, 1),
                       "Valid": v.get("valid", 0), "Fetched": v.get("fetched", 0)}
                      for k, v in sq.items()]
            sq_data.sort(key=lambda x: -x["Quality %"])
            fig2 = px.bar(sq_data, x="Source", y="Quality %",
                         color="Quality %", color_continuous_scale=["#f87171", "#fbbf24", "#4ade80"],
                         text="Quality %")
            fig2.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#7a8290"), xaxis=dict(gridcolor="#1c2230"),
                yaxis=dict(gridcolor="#1c2230", range=[0, 35]),
                coloraxis_showscale=False, margin=dict(t=10, b=20), height=300,
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No source data yet — run aggregator")

    # ── API ROI ──────────────────────────────────────────────────────────────
    st.markdown('<div class="section-title" style="font-size:18px;margin-top:20px;">API Performance</div>', unsafe_allow_html=True)
    apis = b.get("apis", {})
    if apis:
        api_cols = st.columns(len(apis))
        for idx, (name, data) in enumerate(apis.items()):
            with api_cols[idx]:
                used = data.get("credits_used_this_month", 0)
                limit = data.get("lim", data.get("credits_limit", 0))
                vpc = data.get("value_per_credit", 0.0)
                pct = round(used / limit * 100) if limit else 0
                color = "v-red" if pct > 80 else "v-amber" if pct > 50 else "v-green"
                st.markdown(card(
                    name.upper(),
                    f"{used}/{limit}",
                    color,
                    f"ROI: {vpc:.2f} emails/credit"
                ), unsafe_allow_html=True)
    else:
        st.info("No API data yet — APIs not called this month")

    # ── Circuit breaker + Selenium health ────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    h1, h2, h3, h4 = st.columns(4)
    with h1:
        cb_ok = not cb.get("tripped", False)
        st.markdown(card("Circuit Breaker",
            "OK" if cb_ok else "TRIPPED",
            "v-green" if cb_ok else "v-red",
            f"Sent: {cb.get('sent_today',0)} | Bounced: {cb.get('bounced_today',0)}"
        ), unsafe_allow_html=True)
    with h2:
        sel_ok = rh.get("selenium_working", True)
        st.markdown(card("Selenium",
            "OK" if sel_ok else "BROKEN",
            "v-green" if sel_ok else "v-red",
            rh.get("chromedriver_version", "unknown")[:20]
        ), unsafe_allow_html=True)
    with h3:
        simplify_active = sum(1 for v in simplify_q.values() if not v.get("exhausted"))
        st.markdown(card("Simplify Queue", simplify_active, "v-amber" if simplify_active > 0 else "v-green",
            "pending retry" if simplify_active > 0 else "all resolved"
        ), unsafe_allow_html=True)
    with h4:
        domain_corrections = b.get("domain_corrections", {})
        st.markdown(card("Domain Fixes", len(domain_corrections), "v-cyan",
            "wrong→right mappings"
        ), unsafe_allow_html=True)

    # ── Top domains by confidence ─────────────────────────────────────────────
    if domains:
        st.markdown('<div class="section-title" style="font-size:18px;margin-top:20px;">Top Learned Domains</div>', unsafe_allow_html=True)
        top_domains = sorted(
            [(d, e) for d, e in domains.items() if e.get("email_pattern") and e.get("pattern_confidence", 0) > 0],
            key=lambda x: -x[1].get("pattern_confidence", 0)
        )[:20]
        if top_domains:
            import pandas as pd
            df_d = pd.DataFrame([{
                "Domain": d,
                "Pattern": e.get("email_pattern", ""),
                "Confidence": f"{e.get('pattern_confidence', 0):.0%}",
                "Successes": e.get("pattern_successes", 0),
                "Provider": e.get("mx_provider", "other") or "other",
            } for d, e in top_domains])
            st.dataframe(df_d, use_container_width=True, hide_index=True)

    # ── Companies being tracked ───────────────────────────────────────────────
    flagged = [(k, e) for k, e in companies.items()
               if e.get("rejection_count", 0) >= 2 and not e.get("blacklist")]
    if flagged:
        st.markdown('<div class="section-title" style="font-size:18px;margin-top:20px;color:#fbbf24;">⚠ Companies to Watch (2+ rejections)</div>', unsafe_allow_html=True)
        import pandas as pd
        df_co = pd.DataFrame([{
            "Company": e.get("name", k),
            "Rejections": e.get("rejection_count", 0),
            "Top Reason": max(e.get("rejection_reasons", {"none": 1}), key=e.get("rejection_reasons", {}).get),
            "Outreached": e.get("outreach_count", 0),
        } for k, e in sorted(flagged, key=lambda x: -x[1].get("rejection_count", 0))[:15]])
        st.dataframe(df_co, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    tab1, tab2 = st.tabs(["📊 Applications", "🧠 Pipeline Intelligence"])
    with tab1:
        main()
    with tab2:
        brain_tab()

