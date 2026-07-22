"""
CareFlow — Layer 6: Streamlit Dashboard
Live dashboard showing all customer health, churn risk, and email activity.
Run: streamlit run dashboard/streamlit_app.py
"""

import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

DB_PATH = Path(__file__).parent.parent / "data" / "customers.db"

st.set_page_config(
    page_title="CareFlow — Customer Success",
    page_icon="💚",
    layout="wide",
)

# ── Modern CSS Styling ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    :root {
        --primary: #5b6cff;
        --success: #22c55e;
        --warning: #f59e0b;
        --danger: #ef4444;
        --bg-dark: #0f172a;
        --surface: #1e293b;
        --surface-light: #334155;
        --text: #f1f5f9;
        --text-muted: #94a3b8;
        --line: rgba(148, 163, 184, 0.1);
    }
    
    * {
        margin: 0;
        padding: 0;
    }
    
    body {
        background: linear-gradient(135deg, #0f172a 0%, #1a3a52 100%);
        color: var(--text);
    }
    
    .main {
        padding: 0 !important;
    }
    
    .stApp {
        background: linear-gradient(135deg, #0f172a 0%, #1a3a52 100%);
    }
    
    /* Metric Cards */
    [data-testid="metric-container"] {
        background-color: rgba(30, 41, 59, 0.8);
        padding: 20px !important;
        border-radius: 16px;
        border: 1px solid var(--line);
        backdrop-filter: blur(10px);
        transition: all 0.3s ease;
    }
    
    [data-testid="metric-container"]:hover {
        transform: translateY(-4px);
        border-color: var(--primary);
        box-shadow: 0 12px 24px rgba(91, 108, 255, 0.2);
    }
    
    /* Headers */
    h1, h2, h3, h4, h5, h6 {
        color: #f1f5f9 !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }
    
    h1 {
        font-size: 2.5rem !important;
        margin-top: 0 !important;
        background: linear-gradient(135deg, #fbbf24 0%, #22c55e 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    
    /* Divider */
    hr {
        border: none;
        height: 1px;
        background: var(--line);
        margin: 24px 0 !important;
    }
    
    /* Dataframe */
    [data-testid="dataframe"] {
        border-radius: 16px !important;
        overflow: hidden;
    }
    
    .dataframe {
        background-color: rgba(30, 41, 59, 0.6) !important;
        border: 1px solid var(--line) !important;
    }
    
    /* Info Box */
    [data-testid="stAlert"] {
        background-color: rgba(30, 41, 59, 0.9);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 16px;
    }
    
    /* Subheader */
    .stSubheader {
        color: #f1f5f9 !important;
        font-size: 1.5rem !important;
        font-weight: 600 !important;
        margin-top: 24px !important;
        margin-bottom: 16px !important;
    }
    
    .stCaption {
        color: var(--text-muted) !important;
        font-size: 0.92rem !important;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=30)
def load_scores():
    conn = sqlite3.connect(DB_PATH)
    df = conn.execute("""
        SELECT h.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, h.score, h.category, h.explanation, h.scored_at
        FROM health_score_history h
        JOIN customers c ON c.customer_id = h.customer_id
        WHERE h.scored_at = (
            SELECT MAX(scored_at) FROM health_score_history WHERE customer_id = h.customer_id
        )
        ORDER BY h.score ASC
    """).fetchall()
    conn.close()
    return pd.DataFrame(df, columns=[
        "customer_id","organization_name","organization_type",
        "contract_value_usd","health_score","category","explanation","scored_at"
    ])


@st.cache_data(ttl=30)
def load_churn():
    conn = sqlite3.connect(DB_PATH)
    df = conn.execute("""
        SELECT p.customer_id, c.organization_name, c.organization_type,
               c.contract_value_usd, p.churn_probability, p.risk_label, p.shap_explanation
        FROM churn_predictions p
        JOIN customers c ON c.customer_id = p.customer_id
        WHERE p.predicted_at = (
            SELECT MAX(predicted_at) FROM churn_predictions WHERE customer_id = p.customer_id
        )
        ORDER BY p.churn_probability DESC
    """).fetchall()
    conn.close()
    return pd.DataFrame(df, columns=[
        "customer_id","organization_name","organization_type",
        "contract_value_usd","churn_probability","risk_label","shap_explanation"
    ])


@st.cache_data(ttl=30)
def load_customers():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM customers", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_emails():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM email_log ORDER BY sent_at DESC", conn)
    conn.close()
    return df


# ── Load Data ─────────────────────────────────────────────────────────────────
scores_df  = load_scores()
churn_df   = load_churn()
customers  = load_customers()
emails_df  = load_emails()

# ── Header ────────────────────────────────────────────────────────────────────
col_header_left, col_header_right = st.columns([1, 4])

with col_header_left:
    st.markdown("## 💚 **CareFlow**")

with col_header_right:
    st.markdown(f"<p style='color: #94a3b8; margin-top: 18px; font-size: 0.9rem;'>Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Real-time Health & Churn Analytics</p>", unsafe_allow_html=True)

st.divider()

# ── KPI Row ───────────────────────────────────────────────────────────────────
total = len(scores_df)
healthy  = len(scores_df[scores_df["category"] == "Healthy"])
at_risk  = len(scores_df[scores_df["category"] == "At Risk"])
critical = len(scores_df[scores_df["category"] == "Critical"])
high_churn = len(churn_df[churn_df["risk_label"] == "High Risk"]) if len(churn_df) else 0

col1, col2, col3, col4, col5 = st.columns(5, gap="medium")

with col1:
    st.metric("📊 Total Customers", total, delta=None)
with col2:
    st.metric("✅ Healthy", healthy, delta=healthy)
with col3:
    st.metric("⚠️ At Risk", at_risk, delta=at_risk)
with col4:
    st.metric("🔴 Critical", critical, delta=critical)
with col5:
    st.metric("⚡ High Churn Risk", high_churn, delta=high_churn)

st.divider()

# ── Interactive Charts Row ────────────────────────────────────────────────────
col_left, col_right = st.columns(2, gap="large")

with col_left:
    st.subheader("📊 Health Score Distribution")
    if len(scores_df):
        dist = scores_df["category"].value_counts().reindex(["Healthy", "At Risk", "Critical"]).fillna(0)
        fig = go.Figure(data=[
            go.Bar(
                y=dist.index,
                x=dist.values,
                orientation='h',
                marker=dict(
                    color=['#22c55e', '#f59e0b', '#ef4444'],
                    line=dict(color='rgba(148, 163, 184, 0.2)', width=1)
                ),
                text=dist.values,
                textposition='outside',
                hovertemplate='<b>%{y}</b><br>Count: %{x}<extra></extra>'
            )
        ])
        fig.update_layout(
            plot_bgcolor='rgba(30, 41, 59, 0.5)',
            paper_bgcolor='rgba(0, 0, 0, 0)',
            font=dict(family="Inter, sans-serif", color="#f1f5f9"),
            margin=dict(l=0, r=0, t=0, b=0),
            height=300,
            xaxis=dict(
                showgrid=True,
                gridwidth=1,
                gridcolor='rgba(148, 163, 184, 0.1)',
                zeroline=False
            ),
            yaxis=dict(showgrid=False)
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

with col_right:
    st.subheader("⚡ Churn Risk Pipeline")
    if len(churn_df):
        risk_order = ["High Risk", "Medium Risk", "Low Risk"]
        risk_dist = churn_df["risk_label"].value_counts().reindex(risk_order).fillna(0)
        fig = go.Figure(data=[
            go.Bar(
                y=risk_dist.index,
                x=risk_dist.values,
                orientation='h',
                marker=dict(
                    color=['#ef4444', '#f59e0b', '#22c55e'],
                    line=dict(color='rgba(148, 163, 184, 0.2)', width=1)
                ),
                text=risk_dist.values,
                textposition='outside',
                hovertemplate='<b>%{y}</b><br>Count: %{x}<extra></extra>'
            )
        ])
        fig.update_layout(
            plot_bgcolor='rgba(30, 41, 59, 0.5)',
            paper_bgcolor='rgba(0, 0, 0, 0)',
            font=dict(family="Inter, sans-serif", color="#f1f5f9"),
            margin=dict(l=0, r=0, t=0, b=0),
            height=300,
            xaxis=dict(
                showgrid=True,
                gridwidth=1,
                gridcolor='rgba(148, 163, 184, 0.1)',
                zeroline=False
            ),
            yaxis=dict(showgrid=False)
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.divider()

# ── Onboarding Funnel ─────────────────────────────────────────────────────────
st.subheader("🚀 Onboarding Funnel")
if len(customers):
    bins   = [0, 25, 50, 75, 100]
    labels = ["0–25%", "25–50%", "50–75%", "75–100%"]
    customers["onboarding_stage"] = pd.cut(
        customers["onboarding_completion_percentage"], bins=bins, labels=labels, include_lowest=True
    )
    funnel = customers["onboarding_stage"].value_counts().sort_index().reset_index()
    funnel.columns = ["Stage", "Count"]
    
    fig = go.Figure(data=[
        go.Funnel(
            y=funnel["Stage"],
            x=funnel["Count"],
            marker=dict(
                color=funnel["Count"],
                colorscale='Teal',
                line=dict(color='rgba(148, 163, 184, 0.2)', width=1)
            ),
            textposition="inside",
            textinfo="value+percent initial",
            hovertemplate='<b>%{y}</b><br>Customers: %{x}<extra></extra>'
        )
    ])
    fig.update_layout(
        plot_bgcolor='rgba(30, 41, 59, 0.5)',
        paper_bgcolor='rgba(0, 0, 0, 0)',
        font=dict(family="Inter, sans-serif", color="#f1f5f9"),
        margin=dict(l=0, r=0, t=0, b=0),
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.divider()

# ── Health Score History Trend ────────────────────────────────────────────────
st.subheader("📈 Average Health Score Trend")
conn = sqlite3.connect(DB_PATH)
history = pd.read_sql("""
    SELECT DATE(scored_at) as date, AVG(score) as avg_score
    FROM health_score_history
    GROUP BY DATE(scored_at)
    ORDER BY date ASC
""", conn)
conn.close()

if len(history) > 1:
    fig = go.Figure(data=[
        go.Scatter(
            x=history["date"],
            y=history["avg_score"],
            mode='lines+markers',
            name='Avg Health Score',
            line=dict(color='#22c55e', width=3),
            marker=dict(size=8, line=dict(color='#f1f5f9', width=1)),
            fill='tozeroy',
            fillcolor='rgba(34, 197, 94, 0.2)',
            hovertemplate='<b>%{x}</b><br>Score: %{y:.1f}<extra></extra>'
        )
    ])
    fig.update_layout(
        plot_bgcolor='rgba(30, 41, 59, 0.5)',
        paper_bgcolor='rgba(0, 0, 0, 0)',
        font=dict(family="Inter, sans-serif", color="#f1f5f9"),
        margin=dict(l=0, r=0, t=0, b=20),
        height=320,
        xaxis=dict(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(148, 163, 184, 0.1)',
        ),
        yaxis=dict(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(148, 163, 184, 0.1)',
        ),
        hovermode='x unified'
    )
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
else:
    st.info("Run scoring multiple times to see the trend line develop.")

st.divider()

# ── Top 10 At-Risk Accounts ───────────────────────────────────────────────────
st.subheader("🔴 Top 10 At-Risk Accounts")
if len(scores_df):
    at_risk_table = scores_df[scores_df["category"].isin(["Critical","At Risk"])].head(10)[[
        "customer_id","organization_name","organization_type",
        "health_score","category","contract_value_usd","explanation"
    ]].rename(columns={
        "customer_id": "ID",
        "organization_name": "Organization",
        "organization_type": "Type",
        "health_score": "Score",
        "category": "Status",
        "contract_value_usd": "Contract $",
        "explanation": "Reason",
    })
    
    # Format for display
    at_risk_table_display = at_risk_table.copy()
    at_risk_table_display["Contract $"] = at_risk_table_display["Contract $"].apply(lambda x: f"${x:,.0f}")
    at_risk_table_display["Score"] = at_risk_table_display["Score"].apply(lambda x: f"{x:.0f}/100")
    
    st.dataframe(at_risk_table_display, use_container_width=True, hide_index=True, height=400)
else:
    st.info("No at-risk accounts detected.")

st.divider()

# ── Churn Predictions ─────────────────────────────────────────────────────────
st.subheader("⚠️ High Churn Risk Accounts")
if len(churn_df):
    high = churn_df[churn_df["risk_label"] == "High Risk"][[
        "customer_id","organization_name","churn_probability","risk_label","shap_explanation","contract_value_usd"
    ]].head(10).copy()
    
    high.columns = ["ID", "Organization", "Churn Prob", "Risk", "Explanation", "Contract $"]
    high["Churn Prob"] = high["Churn Prob"].apply(lambda x: f"{x:.0%}")
    high["Contract $"] = high["Contract $"].apply(lambda x: f"${x:,.0f}")
    
    st.dataframe(high, use_container_width=True, hide_index=True, height=400)
else:
    st.info("No high-risk churn predictions yet.")

st.divider()

# ── Email Log ─────────────────────────────────────────────────────────────────
st.subheader("📧 Recent Engagement Emails")
if len(emails_df):
    email_display = emails_df[["customer_id","email_type","subject","sent_at"]].head(20).copy()
    email_display.columns = ["Customer ID", "Type", "Subject", "Sent At"]
    email_display["Sent At"] = pd.to_datetime(email_display["Sent At"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(email_display, use_container_width=True, hide_index=True, height=400)
else:
    st.info("No emails generated yet. Emails appear here after engagement campaigns run.")

st.divider()

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style='text-align: center; color: #94a3b8; font-size: 0.92rem; padding: 24px 0;'>
    <p>💚 <strong>CareFlow v1.0</strong> — Built for Customer Success & Analytics</p>
    <p>Real-time health scoring • Churn prediction • Engagement automation</p>
</div>
""", unsafe_allow_html=True)
