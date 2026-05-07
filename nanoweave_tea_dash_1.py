"""
Nanoweave TEA Dashboard
========================
Techno-Economic Assessment — Plasma Biorefinery

HOW TO RUN:
  1. Open this file in VS Code
  2. Open the terminal (Ctrl + `)
  3. Run:  pip install dash plotly pandas
  4. Run:  python nanoweave_tea_dash.py
  5. Open your browser at:  http://127.0.0.1:8050

All data sourced from Nanoweave internal TEA documents:
  - Key Figures 50TPD CVL version
  - CHP One-Pager
  - Biomass Composition Sheet
  - Output Sheet
"""

# ── Imports ────────────────────────────────────────────────────────────────────
import math
import io
import os
import tempfile
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc

# ReportLab — PDF generation
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.platypus import Image as RLImage

# Install check helper — tells user exactly what to install if missing
try:
    import dash_bootstrap_components as dbc
except ImportError:
    raise ImportError(
        "\n\nMissing package. Run this in your terminal:\n"
        "  pip install dash dash-bootstrap-components plotly pandas\n"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DATA TABLES
# All numbers sourced from Nanoweave TEA documents — do not change without
# updating the source reference in the comment next to each value.
# ══════════════════════════════════════════════════════════════════════════════

# Feedstock parameters — from Biomass Composition Sheet & CHP One-Pager
BIOMASS_DATA = {
    "pineapple": {
        "label":        "🍍 Pineapple Leaf Stubble",
        "moisture":      0.60,    # 60% as-received  [CHP One-Pager]
        "cellulose_dm":  0.43,    # 43% of dry matter [Biomass Composition Sheet]
        "lignin_dm":     0.04,
        "ash_dm":        0.10,
        "lhv_gj_t_dry":  17.0,   # GJ/t dry biomass  [literature]
        "n_fixation":    0.0093,  # kg N / kg DM      [plasma model]
        "note":          "60% moisture · 43% cellulose DM · LHV 17 GJ/t dry",
    },
    "efb": {
        "label":        "🌴 Palm Oil EFB",
        "moisture":      0.50,
        "cellulose_dm":  0.34,    # Lab-validated — Kappa 1 achieved [Key Figures]
        "lignin_dm":     0.10,
        "ash_dm":        0.08,
        "lhv_gj_t_dry":  18.8,
        "n_fixation":    0.0093,
        "note":          "50% moisture · 34% cellulose DM · Primary TEA feedstock",
    },
    "sugarcane": {
        "label":        "🎋 Sugar Cane Rastrojo",
        "moisture":      0.50,
        "cellulose_dm":  0.42,    # Bagasse literature value
        "lignin_dm":     0.22,
        "ash_dm":        0.04,
        "lhv_gj_t_dry":  17.5,
        "n_fixation":    0.0080,
        "note":          "50% moisture · 42% cellulose DM · High lignin — good plasma substrate",
    },
    "rice": {
        "label":        "🌾 Rice Rastrojo",
        "moisture":      0.10,    # Dry-harvested
        "cellulose_dm":  0.38,
        "lignin_dm":     0.13,
        "ash_dm":        0.18,    # High silica — plasma process unaffected
        "lhv_gj_t_dry":  14.5,
        "n_fixation":    0.0070,
        "note":          "10% moisture · High silica (18% DM) · Plasma unaffected by silica",
    },
}

# Equipment list — from Key Figures TEA sheet ($1.5M base per 50TPD module)
# Scaling model: Fixed site cost + linear modules
#   "site"   → qty = 1  (paid once — shared infrastructure)
#   "torch"  → qty = n_modules (1 plasma torch per module — key cost driver)
#   "module" → qty = qty_per_module × n_modules (scales linearly)
#
# Each tuple: (name, unit_cost, qty_per_module, scales_with, category, chp_only)
#   chp_only = True  → item excluded when power_mode == "grid"
#              False → always included
EQUIPMENT = [
    # (name,                    unit_cost,  qty_per_module, scales_with,  category,       chp_only)
    ("Plasma Torch 100kW",       200_000,    1,             "torch",      "Process Core", False),
    ("Double Disk Refiner",       70_000,    2,             "module",     "Process",      False),
    ("Flash Dryer (steam)",       35_000,    2,             "module",     "Process",      False),
    ("Boiler (CHP)",             500_000,    1,             "module",     "CHP System",   True),   # 1 per module · $500K · CHP/Hybrid only
    ("Steam Turbine (CHP)",      500_000,    1,             "module",     "CHP System",   True),   # 1 per module · $500K · CHP/Hybrid only
    ("Cellulose Reactor",        250_000,    1,             "module",     "Process",      False),
    ("Plant Electrical System",  250_000,    1,             "site",       "Site Fixed",   False),
    ("Tanks",                     30_000,    1,             "site",       "Site Fixed",   False),
    ("Conveyors",                 20_000,    1,             "site",       "Site Fixed",   False),
    ("Screens",                   15_000,    1,             "site",       "Site Fixed",   False),
]

# Energy per 50TPD module — from Output Sheet
# Flash dryer uses residual CHP steam → NOT charged as electricity
ENERGY_KWH_DAY = {
    "Refining":           1_081,   # kWh/day [Output Sheet]
    "Plasma (100kW)":     3_388,   # kWh/day [Output Sheet]
    "Mech. Separation":     600,   # kWh/day — mechanical only; thermal from steam
}
TOTAL_ENERGY_MODULE = sum(ENERGY_KWH_DAY.values())   # 5,069 kWh/day

# CHP efficiencies — from CHP One-Pager
CHP_ELEC_EFF = 0.20   # 20% of thermal → electricity
CHP_HEAT_EFF = 0.50   # 50% of thermal → heat (flash drying + feedstock drying)

# OPEX constants — from Key Figures & Output Sheets
OP_DAYS          = 330       # operating days/year (35 days scheduled maintenance)
PROC_EFFICIENCY  = 0.80      # process efficiency [Input Sheet]
LABOR_PER_MODULE = 350_000   # $/yr — 8 operators + supervisor [Output Sheet]
NAOH_T_PER_MOD   = 1.977     # ton NaOH/day per module [Cellulose Process Sheet]
NAOH_PRICE       = 450       # $/ton
WATER_PER_MOD    = 257.25    # $/day per module [Output Sheet — 99% water recovery]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_usd(n, decimals=0):
    """Format a number as a compact USD string: $1.23M, $456K, $789"""
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "—"
    if abs(n) >= 1e6:
        return f"${n/1e6:,.2f}M"
    if abs(n) >= 1e3:
        return f"${n/1e3:,.0f}K"
    return f"${n:,.{decimals}f}"


def run_calculations(biomass_total, biomass_key, power_mode, elec_price,
                     cell_price, fert_price, depr_years, logistics_cost=6.0):
    """
    Core calculation engine.
    Takes all user inputs, returns a dict with every computed result.
    """
    bd = BIOMASS_DATA[biomass_key]
    dm = 1 - bd["moisture"]   # dry matter fraction

    # ── CHP electricity yield ──────────────────────────────────────────────
    # kWh of electricity generated per ton of wet biomass burned
    elec_kwh_per_t_wet = dm * (bd["lhv_gj_t_dry"] * 1000 / 3.6) * CHP_ELEC_EFF

    # ── Module sizing ──────────────────────────────────────────────────────
    if power_mode == "chp":
        # Reserve biomass for CHP before allocating to process modules
        # chp_per_module = how many t/day wet biomass needed to power 1 module
        chp_per_module = TOTAL_ENERGY_MODULE / elec_kwh_per_t_wet
        # Total biomass = (50 t process + chp_per_module) × n_modules
        n_modules   = max(1, math.ceil(biomass_total / (50 + chp_per_module)))
        biomass_chp = n_modules * chp_per_module
        elec_cost   = 0.0

    elif power_mode == "grid":
        chp_per_module = 0
        n_modules      = max(1, math.ceil(biomass_total / 50))
        biomass_chp    = 0
        elec_cost      = elec_price

    else:  # hybrid — CHP covers 50%, grid covers 50%
        chp_per_module = (TOTAL_ENERGY_MODULE * 0.5) / elec_kwh_per_t_wet
        n_modules      = max(1, math.ceil(biomass_total / (50 + chp_per_module)))
        biomass_chp    = n_modules * chp_per_module
        elec_cost      = elec_price * 0.5   # only half from grid

    biomass_process = n_modules * 50

    # ── Process outputs ────────────────────────────────────────────────────
    cell_per_mod_day = 50 * dm * bd["cellulose_dm"] * PROC_EFFICIENCY  # t/day
    total_cell_day   = n_modules * cell_per_mod_day
    total_cell_yr    = total_cell_day * OP_DAYS

    # ── Fertilizer — residual biomass + N-fixation stream combined ─────────
    # After cellulose extraction the remaining dry-matter fraction contains:
    #   · Lignin-derived humic & fulvic acids
    #   · Mineral fraction (ash, silica, potassium, phosphorus)
    #   · Nitrogen-rich liquid from plasma oxidation (N-fixation)
    # These combine into a single organic fertilizer product.
    #
    # Residual DM per module per day:
    #   = DM input − cellulose extracted
    #   = 50 × dm  −  cell_per_mod_day
    residual_dm_per_mod_day = (50 * dm) - cell_per_mod_day   # t residual DM/day
    n_per_mod_day           = 50 * dm * bd["n_fixation"]      # t N/day (liquid stream)
    # Total fertilizer = residual biomass carrier + N stream
    fert_per_mod_day        = residual_dm_per_mod_day + n_per_mod_day
    total_fert_day          = n_modules * fert_per_mod_day
    total_fert_yr           = total_fert_day * OP_DAYS
    # N content of fertilizer (for pricing — fert_price is $/t N-equivalent)
    total_n_yr              = n_modules * n_per_mod_day * OP_DAYS

    # ── CAPEX — Fixed site + linear modules ───────────────────────────────
    # Boiler (chp_only=True) is excluded in Grid mode — no CHP means no boiler.
    capex_rows = []
    for name, unit_cost, qty_pm, scale, cat, chp_only in EQUIPMENT:
        if chp_only and power_mode == "grid":
            continue   # skip boiler in Grid-only configuration
        if scale == "site":
            qty = 1
        elif scale == "torch":
            qty = n_modules
        else:
            qty = qty_pm * n_modules
        total  = unit_cost * qty
        naive  = unit_cost * qty_pm * n_modules    # pure-linear counterfactual
        saving = naive - total
        capex_rows.append({
            "Category":  cat,
            "Equipment": name,
            "Unit ($)":  unit_cost,
            "Qty":       qty,
            "Scale":     scale,
            "Total":     total,
            "Naive":     naive,
            "Saving":    saving,
        })

    df_capex        = pd.DataFrame(capex_rows)
    capex_process   = df_capex["Total"].sum()
    capex_naive     = df_capex["Naive"].sum()
    capex_site_save = capex_naive - capex_process

    # CHP CAPEX is now fully captured in the EQUIPMENT table
    # (Boiler $500K + Steam Turbine $500K = $1M per module, CHP/Hybrid only)
    # We still compute capex_chp as the sum of chp_only items for reporting
    chp_mask  = df_capex["Category"] == "CHP System"
    capex_chp = df_capex.loc[chp_mask, "Total"].sum() if chp_mask.any() else 0

    capex_total     = capex_process + capex_chp
    capex_intensity = capex_total / total_cell_yr if total_cell_yr > 0 else 0

    # ── OPEX ──────────────────────────────────────────────────────────────
    energy_yr    = n_modules * TOTAL_ENERGY_MODULE * OP_DAYS * elec_cost
    naoh_yr      = n_modules * NAOH_T_PER_MOD * NAOH_PRICE * OP_DAYS
    labor_yr     = n_modules * LABOR_PER_MODULE
    water_yr     = n_modules * WATER_PER_MOD * OP_DAYS
    depr_yr      = capex_process / depr_years
    logistics_yr = logistics_cost * biomass_process * OP_DAYS
    total_opex   = energy_yr + naoh_yr + labor_yr + water_yr + depr_yr + logistics_yr
    opex_per_t   = total_opex / total_cell_yr if total_cell_yr > 0 else 0

    # ── Revenue & Margin ──────────────────────────────────────────────────
    rev_cell_mid = total_cell_yr * cell_price
    rev_cell_lo  = total_cell_yr * 600
    rev_cell_hi  = total_cell_yr * 1_600
    # Fertilizer revenue: priced on N-content equivalent ($/t N) × total N tonnes
    # The full fertilizer product (residual biomass + N stream) is valued by its
    # N content — the humic/fulvic acids and minerals add agronomic value but
    # are conservatively not separately priced in the base model.
    rev_fert     = total_n_yr * fert_price
    rev_total    = rev_cell_mid + rev_fert
    margin       = rev_total - total_opex
    margin_pct   = (margin / rev_total * 100) if rev_total > 0 else 0
    payback      = capex_process / margin if margin > 0 else None

    # ── Environmental ─────────────────────────────────────────────────────
    co2_avoided = biomass_process * dm * OP_DAYS * 1.4   # t CO2/yr
    trees_saved = round(co2_avoided / 0.9)
    hectares    = round(n_modules * 882)                  # from Key Figures sheet
    co2_fert    = n_modules * 400
    co2_total   = co2_avoided + co2_fert

    # ── CHP heat balance ──────────────────────────────────────────────────
    heat_kwh_per_t  = dm * (bd["lhv_gj_t_dry"] * 1000 / 3.6) * CHP_HEAT_EFF
    heat_generated  = biomass_chp * heat_kwh_per_t
    heat_for_drying = total_cell_day * 700   # ~700 kWh heat/t cellulose
    heat_surplus    = max(0, heat_generated - heat_for_drying)

    return {
        # sizing
        "n_modules":        n_modules,
        "biomass_process":  biomass_process,
        "biomass_chp":      biomass_chp,
        "chp_per_module":   chp_per_module if power_mode != "grid" else 0,
        "elec_cost":        elec_cost,
        # outputs
        "total_cell_day":          total_cell_day,
        "total_cell_yr":           total_cell_yr,
        "total_n_yr":              total_n_yr,
        "total_fert_day":          total_fert_day,
        "total_fert_yr":           total_fert_yr,
        "residual_dm_per_mod_day": residual_dm_per_mod_day,
        "fert_per_mod_day":        fert_per_mod_day,
        # capex
        "df_capex":         df_capex,
        "capex_process":    capex_process,
        "capex_naive":      capex_naive,
        "capex_site_save":  capex_site_save,
        "capex_chp":        capex_chp,
        "capex_total":      capex_total,
        "capex_intensity":  capex_intensity,
        # opex
        "energy_yr":        energy_yr,
        "naoh_yr":          naoh_yr,
        "labor_yr":         labor_yr,
        "water_yr":         water_yr,
        "depr_yr":          depr_yr,
        "logistics_yr":     logistics_yr,
        "total_opex":       total_opex,
        "opex_per_t":       opex_per_t,
        # revenue
        "rev_cell_lo":      rev_cell_lo,
        "rev_cell_mid":     rev_cell_mid,
        "rev_cell_hi":      rev_cell_hi,
        "rev_fert":         rev_fert,
        "rev_total":        rev_total,
        "margin":           margin,
        "margin_pct":       margin_pct,
        "payback":          payback,
        # environmental
        "co2_avoided":      co2_avoided,
        "trees_saved":      trees_saved,
        "hectares":         hectares,
        "co2_total":        co2_total,
        # energy balance
        "heat_generated":   heat_generated,
        "heat_for_drying":  heat_for_drying,
        "heat_surplus":     heat_surplus,
        "elec_kwh_per_t_wet": elec_kwh_per_t_wet,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DASH APP LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

# Colour palette
C = {
    "bg":      "#0a0f0d",
    "surface": "#111914",
    "surface2":"#162118",
    "card":    "#141f18",
    "border":  "#2d4a3a",
    "green":   "#3ddc84",
    "green2":  "#1a8a4a",
    "blue":    "#0015ff",
    "amber":   "#f5a623",
    "red":     "#e05252",
    "text":    "#f0f7f2",
    "text2":   "#ff83f1",
}

CARD_STYLE = {
    "background": C["card"],
    "border":     f"1px solid {C['border']}",
    "borderRadius":"10px",
    "padding":    "16px 18px",
}

LABEL_STYLE = {
    "fontSize": "10px",
    "fontFamily": "monospace",
    "color": C["text2"],
    "letterSpacing": "1.5px",
    "textTransform": "uppercase",
    "marginBottom": "6px",
}

VALUE_STYLE = {
    "fontSize": "26px",
    "fontFamily": "monospace",
    "fontWeight": "700",
    "color": C["green"],
    "letterSpacing": "-1px",
    "lineHeight": "1",
}

SUB_STYLE = {
    "fontSize": "11px",
    "color": C["text2"],
    "fontFamily": "monospace",
    "marginTop": "4px",
}

SECTION_STYLE = {
    "fontFamily": "monospace",
    "fontSize": "11px",
    "letterSpacing": "2px",
    "color": C["text2"],
    "textTransform": "uppercase",
    "borderBottom": f"1px solid {C['border']}",
    "paddingBottom": "8px",
    "marginBottom": "16px",
    "marginTop": "28px",
}

INPUT_STYLE = {
    "background": C["surface2"],
    "border": f"1px solid {C['border']}",
    "borderRadius": "8px",
    "color": C["text"],
    "padding": "8px 12px",
    "fontFamily": "monospace",
    "fontSize": "13px",
    "width": "100%",
}

def kpi_card(label, value_id, sub_id, value_color=None):
    """Reusable KPI card component."""
    val_style = {**VALUE_STYLE, "color": value_color or C["green"]}
    return html.Div([
        html.Div(label, style=LABEL_STYLE),
        html.Div("—", id=value_id, style=val_style),
        html.Div("—", id=sub_id,   style=SUB_STYLE),
    ], style=CARD_STYLE)


app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    title="Nanoweave TEA",
)
server = app.server  # expose Flask server for Gunicorn

app.layout = html.Div(style={"background": C["bg"], "minHeight": "100vh",
                              "fontFamily": "'DM Sans', sans-serif", "color": C["text"]}, children=[

    # ── Header ──────────────────────────────────────────────────────────────
    html.Div(style={
        "background": "linear-gradient(135deg,#0a1510,#0f2018,#091510)",
        "borderBottom": f"1px solid {C['border']}",
        "padding": "18px 32px",
        "display": "flex",
        "alignItems": "center",
        "gap": "16px",
        "position": "sticky",
        "top": "0",
        "zIndex": "100",
    }, children=[
        html.Div("N", style={
            "width":"42px","height":"42px","border":f"2px solid {C['green']}",
            "borderRadius":"50%","display":"flex","alignItems":"center",
            "justifyContent":"center","fontFamily":"monospace","fontWeight":"700",
            "color":C["green"],"fontSize":"16px",
            "boxShadow":"0 0 16px rgba(61,220,132,0.3)",
        }),
        html.Div([
            html.Div("NANOWEAVE TEA DASHBOARD", style={
                "fontFamily":"monospace","fontSize":"14px","letterSpacing":"3px",
                "color":C["green"],"fontWeight":"700",
            }),
            html.Div("Techno-Economic Assessment · Plasma Biorefinery · v1.1", style={
                "fontSize":"11px","color":C["text2"],"letterSpacing":"1px","marginTop":"2px",
            }),
        ]),
        html.Div("50 TPD Base Module", style={
            "marginLeft":"auto","fontFamily":"monospace","fontSize":"11px","color":C["text2"],
            "background":C["surface2"],"padding":"5px 12px","borderRadius":"6px",
            "border":f"1px solid {C['border']}",
        }),
    ]),

    # ── Main layout: sidebar + content ──────────────────────────────────────
    html.Div(style={"display":"flex","gap":"0","maxWidth":"1500px","margin":"0 auto"}, children=[

        # ══ SIDEBAR ════════════════════════════════════════════════════════
        html.Div(style={
            "width":"320px","flexShrink":"0",
            "background":C["surface"],"borderRight":f"1px solid {C['border']}",
            "padding":"24px 20px","position":"sticky","top":"75px",
            "height":"calc(100vh - 75px)","overflowY":"auto",
        }, children=[

            html.Div("CONFIGURATION", style={**LABEL_STYLE, "marginBottom":"16px"}),

            # Biomass slider
            html.Div([
                html.Div("Biomass Input", style=LABEL_STYLE),
                html.Div(id="biomass-display", style={
                    "fontFamily":"monospace","fontSize":"22px","color":C["green"],
                    "fontWeight":"700","textAlign":"center","padding":"4px 0",
                }),
                dcc.Slider(
                    id="biomass-slider",
                    min=50, max=1000, step=50, value=200,
                    marks={50:"50", 250:"250", 500:"500", 750:"750", 1000:"1000"},
                    tooltip={"placement":"bottom","always_visible":False},
                ),
            ], style={"marginBottom":"20px"}),

            # Biomass type
            html.Div([
                html.Div("Biomass Type", style=LABEL_STYLE),
                dcc.Dropdown(
                    id="biomass-type",
                    options=[{"label": v["label"], "value": k}
                             for k, v in BIOMASS_DATA.items()],
                    value="pineapple",
                    clearable=False,
                    style={**INPUT_STYLE, "padding":"2px"},
                ),
            ], style={"marginBottom":"20px"}),

            # Power mode
            html.Div([
                html.Div("Power Configuration", style=LABEL_STYLE),
                dcc.RadioItems(
                    id="power-mode",
                    options=[
                        {"label": " 🔥 Full CHP",          "value": "chp"},
                        {"label": " ⚡ Grid Only",           "value": "grid"},
                        {"label": " ☀️ Hybrid Solar+CHP",   "value": "hybrid"},
                    ],
                    value="chp",
                    labelStyle={"display":"block","marginBottom":"6px",
                                "fontSize":"12px","color":C["text"],"cursor":"pointer"},
                ),
            ], style={"marginBottom":"20px"}),

            # Electricity price (shown only for grid/hybrid)
            html.Div(id="elec-price-group", children=[
                html.Div("Electricity Price ($/kWh)", style=LABEL_STYLE),
                dcc.Input(
                    id="elec-price",
                    type="number", value=0.08, min=0.04, max=0.25, step=0.01,
                    style=INPUT_STYLE,
                ),
            ], style={"marginBottom":"20px"}),

            # Logistics cost
            html.Div([
                html.Div("Biomass Logistics Cost ($/t wet)", style=LABEL_STYLE),
                dcc.Input(
                    id="logistics-cost",
                    type="number", value=6.0, min=0, max=50, step=0.5,
                    style=INPUT_STYLE,
                ),
                html.Div(
                    "CHP auto-switch: logistics > $26/t → use grid",
                    style={"fontSize":"10px","color":C["amber"],"fontFamily":"monospace",
                           "marginTop":"4px"},
                ),
            ], style={"marginBottom":"20px"}),

            html.Hr(style={"borderColor":C["border"],"margin":"16px 0"}),

            html.Div("ECONOMICS", style={**LABEL_STYLE,"marginBottom":"16px"}),

            # Cellulose price
            html.Div([
                html.Div("Cellulose Price ($/ton)", style=LABEL_STYLE),
                dcc.Slider(
                    id="cell-price",
                    min=600, max=1600, step=50, value=900,
                    marks={600:"$600",900:"$900",1200:"$1,200",1600:"$1,600"},
                    tooltip={"placement":"bottom","always_visible":False},
                ),
            ], style={"marginBottom":"20px"}),

            # Fertilizer price
            html.Div([
                html.Div("Fertilizer Price ($/ton N)", style=LABEL_STYLE),
                dcc.Slider(
                    id="fert-price",
                    min=100, max=600, step=25, value=350,
                    marks={100:"$100",350:"$350",600:"$600"},
                    tooltip={"placement":"bottom","always_visible":False},
                ),
            ], style={"marginBottom":"20px"}),

            # Depreciation
            html.Div([
                html.Div("Depreciation Horizon", style=LABEL_STYLE),
                dcc.Dropdown(
                    id="depr-years",
                    options=[{"label":f"{y} years","value":y} for y in [10,15,20]],
                    value=15, clearable=False,
                    style={**INPUT_STYLE,"padding":"2px"},
                ),
            ], style={"marginBottom":"20px"}),

            html.Hr(style={"borderColor":C["border"],"margin":"16px 0"}),
            html.Div("Source: Nanoweave Key Figures v50TPD · CHP One-Pager · Biomass Composition Sheet",
                     style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace",
                            "lineHeight":"1.6"}),
        ]),

        # ══ MAIN CONTENT ═══════════════════════════════════════════════════
        html.Div(style={"flex":"1","padding":"28px 28px","overflowX":"hidden"}, children=[

            # ─ Feedstock note ───────────────────────────────────────────────
            html.Div(id="feedstock-note", style={
                "background":"rgba(61,220,132,0.06)","border":f"1px solid rgba(61,220,132,0.2)",
                "borderRadius":"8px","padding":"10px 16px","marginBottom":"20px",
                "fontSize":"12px","color":C["text2"],"fontFamily":"monospace","lineHeight":"1.6",
            }),

            # ─ System Sizing ────────────────────────────────────────────────
            html.Div("SYSTEM SIZING", style=SECTION_STYLE),
            html.Div(style={"display":"grid","gridTemplateColumns":"repeat(4,1fr)","gap":"12px",
                            "marginBottom":"24px"}, children=[
                kpi_card("Modules Required",  "kpi-modules",  "kpi-modules-sub"),
                kpi_card("Plasma Torches",     "kpi-torches",  "kpi-torches-sub",  C["blue"]),
                kpi_card("Cellulose Output",   "kpi-cellulose","kpi-cellulose-sub", C["text"]),
                kpi_card("Biomass for CHP",    "kpi-chp",      "kpi-chp-sub",       C["amber"]),
            ]),

            # ─ CAPEX ─────────────────────────────────────────────────────
            html.Div("CAPEX BREAKDOWN", style=SECTION_STYLE),
            html.Div(style={"display":"grid","gridTemplateColumns":"1.4fr 1fr","gap":"16px",
                            "marginBottom":"24px"}, children=[

                # Equipment table
                html.Div([
                    html.Div("Equipment Itemization", style={**LABEL_STYLE,"marginBottom":"10px"}),
                    html.Div(id="capex-table"),
                ], style=CARD_STYLE),

                # Summary + donut
                html.Div([
                    html.Div("Cost Summary", style={**LABEL_STYLE,"marginBottom":"10px"}),
                    html.Div(id="capex-summary"),
                    dcc.Graph(id="capex-donut", style={"height":"200px"},
                              config={"displayModeBar":False}),
                ], style=CARD_STYLE),
            ]),

            html.Div(id="capex-note", style={
                "fontSize":"11px","color":C["text2"],"fontFamily":"monospace",
                "marginBottom":"24px",
            }),

            # ─ OPEX & Revenue ─────────────────────────────────────────────
            html.Div("OPEX & REVENUE (Annual · All Modules)", style=SECTION_STYLE),
            html.Div(style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"16px",
                            "marginBottom":"24px"}, children=[

                # OPEX
                html.Div([
                    html.Div("OPEX Structure", style={**LABEL_STYLE,"marginBottom":"10px"}),
                    dcc.Graph(id="opex-bar", style={"height":"100px"},
                              config={"displayModeBar":False}),
                    html.Div(id="opex-table"),
                ], style=CARD_STYLE),

                # Revenue
                html.Div([
                    html.Div("Revenue & EBITDA", style={**LABEL_STYLE,"marginBottom":"10px"}),
                    dcc.Graph(id="revenue-waterfall", style={"height":"320px"},
                              config={"displayModeBar":False}),
                    html.Div(id="ebitda-note", style={
                        "background":"rgba(0,191,255,0.06)",
                        "border":"1px solid rgba(0,191,255,0.2)",
                        "borderRadius":"8px","padding":"10px 14px",
                        "fontSize":"11px","color":C["text2"],"fontFamily":"monospace",
                        "lineHeight":"1.6","marginTop":"10px",
                    }),
                ], style=CARD_STYLE),
            ]),

            # ─ Environmental ─────────────────────────────────────────────
            html.Div("ENVIRONMENTAL & OUTPUT SUMMARY", style=SECTION_STYLE),
            html.Div(style={"display":"grid","gridTemplateColumns":"repeat(6,1fr)","gap":"12px",
                            "marginBottom":"24px"}, children=[
                kpi_card("CO₂ Avoided/yr",   "env-co2",      "env-co2-sub",    C["green"]),
                kpi_card("Trees Saved/yr",   "env-trees",    "env-trees-sub",  C["green"]),
                kpi_card("Forest Saved/yr",  "env-ha",       "env-ha-sub",     C["green"]),
                kpi_card("Organic Fertilizer/yr","env-n",        "env-n-sub",      C["amber"]),
                kpi_card("Cellulose/yr",     "env-cell",     "env-cell-sub",   C["text"]),
                kpi_card("Total CO₂ Impact", "env-co2total", "env-co2total-sub",C["blue"]),
            ]),

            # ─ Sensitivity ────────────────────────────────────────────────
            html.Div("PRICE SENSITIVITY — ANNUAL MARGIN", style=SECTION_STYLE),
            html.Div(id="sensitivity-table", style={"marginBottom":"24px"}),

            # ─ Energy Balance ─────────────────────────────────────────────
            html.Div("ENERGY BALANCE (per 50 TPD Module)", style=SECTION_STYLE),
            html.Div(style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"16px",
                            "marginBottom":"40px"}, children=[
                html.Div([
                    html.Div("Electricity Demand", style={**LABEL_STYLE,"marginBottom":"8px"}),
                    dcc.Graph(id="energy-bar", style={"height":"260px"},
                              config={"displayModeBar":False}),
                    html.Div("Flash drying runs on residual CHP steam — thermal load not charged as electricity.",
                             style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace",
                                    "marginTop":"6px"}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("CHP / Power Balance", style={**LABEL_STYLE,"marginBottom":"8px"}),
                    html.Div(id="chp-balance"),
                ], style=CARD_STYLE),
            ]),

            # ═══════════════════════════════════════════════════════════════
            # ─ FRANCHISE MODEL MODULE ──────────────────────────────────────
            # ═══════════════════════════════════════════════════════════════
            html.Div("FRANCHISE MODEL — DEVELOPER CASH FLOW PROJECTION", style=SECTION_STYLE),

            # Franchise inputs row
            html.Div(style={"display":"grid","gridTemplateColumns":"repeat(4,1fr)","gap":"12px",
                            "marginBottom":"16px"}, children=[
                html.Div([
                    html.Div("No. of Franchisees", style=LABEL_STYLE),
                    dcc.Slider(id="fr-franchisees", min=1, max=20, step=1, value=5,
                               marks={1:"1",5:"5",10:"10",15:"15",20:"20"},
                               tooltip={"placement":"bottom","always_visible":False}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Modules per Franchisee", style=LABEL_STYLE),
                    dcc.Slider(id="fr-modules", min=1, max=16, step=1, value=8,
                               marks={1:"1",4:"4",8:"8",12:"12",16:"16"},
                               tooltip={"placement":"bottom","always_visible":False}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Project Lifespan (years)", style=LABEL_STYLE),
                    dcc.Dropdown(id="fr-lifespan",
                                 options=[{"label":f"{y} years","value":y} for y in [10,15,20,25]],
                                 value=15, clearable=False,
                                 style={**INPUT_STYLE,"padding":"2px"}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Franchisee Ramp-up (months/franchisee)", style=LABEL_STYLE),
                    dcc.Slider(id="fr-rampup", min=3, max=24, step=3, value=12,
                               marks={3:"3m",12:"12m",24:"24m"},
                               tooltip={"placement":"bottom","always_visible":False}),
                ], style=CARD_STYLE),
            ]),

            # Developer OPEX inputs
            html.Div(style={"display":"grid","gridTemplateColumns":"repeat(4,1fr)","gap":"12px",
                            "marginBottom":"16px"}, children=[
                html.Div([
                    html.Div("Developer HQ OPEX ($/yr)", style=LABEL_STYLE),
                    dcc.Input(id="fr-hq-opex", type="number", value=800_000, step=50_000,
                              style=INPUT_STYLE),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("License Fee (% of Franchisee EBITDA)", style=LABEL_STYLE),
                    html.Div("Fixed at 5% — per Nanoweave franchise model",
                             style={"fontSize":"11px","color":C["amber"],"fontFamily":"monospace",
                                    "marginTop":"8px","fontWeight":"700"}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Market Cellulose Sell Price ($/t)", style=LABEL_STYLE),
                    dcc.Input(id="fr-sell-price", type="number", value=900, step=50,
                              style=INPUT_STYLE),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Developer CAPEX (HQ + logistics, $)", style=LABEL_STYLE),
                    dcc.Input(id="fr-dev-capex", type="number", value=2_000_000, step=100_000,
                              style=INPUT_STYLE),
                ], style=CARD_STYLE),
            ]),

            # Franchisee price scenarios explanation
            html.Div(style={
                "background":"rgba(245,166,35,0.06)","border":"1px solid rgba(245,166,35,0.2)",
                "borderRadius":"8px","padding":"10px 16px","marginBottom":"16px",
                "fontSize":"11px","color":C["text2"],"fontFamily":"monospace","lineHeight":"1.7",
            }, children=[
                html.Strong("Franchisee Cellulose Buy Price (3 scenarios): ", style={"color":C["amber"]}),
                "Nanoweave buys cellulose from franchisees at OPEX + margin. "
                "Three scenarios shown: ",
                html.Strong("+10%", style={"color":C["green"]}), " (cost-plus low)  ·  ",
                html.Strong("+15%", style={"color":C["blue"]}), " (cost-plus mid)  ·  ",
                html.Strong("+20%", style={"color":C["amber"]}), " (cost-plus high)  "
                "License fee = 5% of franchisee EBITDA per installed module, received by Developer annually.",
            ]),

            # KPI summary row
            html.Div(style={"display":"grid","gridTemplateColumns":"repeat(5,1fr)","gap":"12px",
                            "marginBottom":"16px"}, children=[
                kpi_card("Total Modules (all franchisees)", "fr-kpi-modules",    "fr-kpi-modules-sub"),
                kpi_card("Total Cellulose /yr",             "fr-kpi-cellulose",  "fr-kpi-cellulose-sub", C["text"]),
                kpi_card("License Income /yr (mature)",     "fr-kpi-license",    "fr-kpi-license-sub",   C["amber"]),
                kpi_card("Dev. Margin /yr (mid, mature)",   "fr-kpi-margin",     "fr-kpi-margin-sub",    C["green"]),
                kpi_card("Developer Break-even",            "fr-kpi-bep",        "fr-kpi-bep-sub",       C["blue"]),
            ]),

            # Cash flow chart — main output
            html.Div([
                html.Div("Developer Cumulative Cash Flow — All 3 Buy-Price Scenarios",
                         style={**LABEL_STYLE,"marginBottom":"10px"}),
                dcc.Graph(id="fr-cashflow-chart", style={"height":"420px"},
                          config={"displayModeBar":False}),
            ], style={**CARD_STYLE,"marginBottom":"16px"}),

            # Annual cash flow bar + franchisee price table side by side
            html.Div(style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"16px",
                            "marginBottom":"24px"}, children=[
                html.Div([
                    html.Div("Annual Developer Net Cash Flow (mid scenario)",
                             style={**LABEL_STYLE,"marginBottom":"10px"}),
                    dcc.Graph(id="fr-annual-bar", style={"height":"280px"},
                              config={"displayModeBar":False}),
                ], style=CARD_STYLE),
                html.Div([
                    html.Div("Franchisee Buy Price & Developer Margin per Scenario",
                             style={**LABEL_STYLE,"marginBottom":"10px"}),
                    html.Div(id="fr-price-table"),
                ], style=CARD_STYLE),
            ]),

            # ─ PDF Report Button ───────────────────────────────────────────
            html.Div(style={
                "borderTop":f"1px solid {C['border']}","paddingTop":"20px",
                "marginBottom":"20px","display":"flex","alignItems":"center","gap":"16px",
            }, children=[
                html.Button(
                    "⬇  Generate PDF Report",
                    id="btn-pdf",
                    n_clicks=0,
                    style={
                        "background": C["green2"],
                        "color": C["text"],
                        "border": f"1px solid {C['green']}",
                        "borderRadius": "8px",
                        "padding": "10px 24px",
                        "fontFamily": "monospace",
                        "fontSize": "13px",
                        "fontWeight": "700",
                        "letterSpacing": "1px",
                        "cursor": "pointer",
                        "boxShadow": "0 0 12px rgba(61,220,132,0.25)",
                    }
                ),
                html.Div(id="pdf-status", style={
                    "fontSize":"12px","color":C["text2"],"fontFamily":"monospace",
                }),
                dcc.Download(id="pdf-download"),
            ]),

            # Footer
            html.Div("Nanoweave TEA Dashboard v1.2 — Data: Key Figures 50TPD CVL · CHP One-Pager · "
                     "Biomass Composition Sheet · Output Sheet · Provisional patent filed · Confidential",
                     style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace",
                            "marginBottom":"20px"}),
        ]),
    ]),
])


# ══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# One main callback reads all inputs → runs calculations → updates all outputs
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    # Sidebar display
    Output("biomass-display",   "children"),
    Output("elec-price-group",  "style"),
    # System sizing KPIs
    Output("kpi-modules",       "children"),
    Output("kpi-modules-sub",   "children"),
    Output("kpi-torches",       "children"),
    Output("kpi-torches-sub",   "children"),
    Output("kpi-cellulose",     "children"),
    Output("kpi-cellulose-sub", "children"),
    Output("kpi-chp",           "children"),
    Output("kpi-chp-sub",       "children"),
    # Feedstock note
    Output("feedstock-note",    "children"),
    # CAPEX
    Output("capex-table",       "children"),
    Output("capex-summary",     "children"),
    Output("capex-donut",       "figure"),
    Output("capex-note",        "children"),
    # OPEX
    Output("opex-bar",          "figure"),
    Output("opex-table",        "children"),
    # Revenue
    Output("revenue-waterfall", "figure"),
    Output("ebitda-note",       "children"),
    # Environmental
    Output("env-co2",           "children"),
    Output("env-co2-sub",       "children"),
    Output("env-trees",         "children"),
    Output("env-trees-sub",     "children"),
    Output("env-ha",            "children"),
    Output("env-ha-sub",        "children"),
    Output("env-n",             "children"),
    Output("env-n-sub",         "children"),
    Output("env-cell",          "children"),
    Output("env-cell-sub",      "children"),
    Output("env-co2total",      "children"),
    Output("env-co2total-sub",  "children"),
    # Sensitivity
    Output("sensitivity-table", "children"),
    # Energy
    Output("energy-bar",        "figure"),
    Output("chp-balance",       "children"),

    # ── Inputs ──
    Input("biomass-slider",  "value"),
    Input("biomass-type",    "value"),
    Input("power-mode",      "value"),
    Input("elec-price",      "value"),
    Input("cell-price",      "value"),
    Input("fert-price",      "value"),
    Input("depr-years",      "value"),
    Input("logistics-cost",  "value"),
)
def update_all(biomass_total, biomass_key, power_mode, elec_price,
               cell_price, fert_price, depr_years, logistics_cost):

    # Safety defaults
    elec_price     = elec_price     or 0.08
    cell_price     = cell_price     or 900
    fert_price     = fert_price     or 350
    depr_years     = depr_years     or 15
    logistics_cost = logistics_cost or 6.0

    r  = run_calculations(biomass_total, biomass_key, power_mode,
                          elec_price, cell_price, fert_price, depr_years,
                          logistics_cost)
    bd = BIOMASS_DATA[biomass_key]

    # ── Sidebar display ────────────────────────────────────────────────────
    biomass_disp   = f"{biomass_total} ton/day"
    elec_grp_style = {"marginBottom":"20px"} if power_mode != "chp" else {"display":"none"}

    # ── KPIs ───────────────────────────────────────────────────────────────
    chp_val = (f"{r['biomass_chp']:.1f} t/day"
               if power_mode != "grid" else "—")
    chp_sub = (f"→ CHP ({r['chp_per_module']:.1f} t/mod)"
               if power_mode != "grid" else "All biomass → pulp")

    # ── Feedstock note ─────────────────────────────────────────────────────
    feed_note = (f"ℹ️  {bd['label']} — {bd['note']}  |  "
                 f"Cellulose DM: {bd['cellulose_dm']*100:.0f}%  |  "
                 f"LHV: {bd['lhv_gj_t_dry']} GJ/t dry  |  "
                 f"Organic fertilizer: residual biomass (humic & fulvic acids + minerals) "
                 f"+ plasma N-fixation stream → "
                 f"{r['fert_per_mod_day']:.1f} t/day/module · "
                 f"{r['total_n_yr']:,.0f} t N equiv./yr")

    # ── CAPEX table ────────────────────────────────────────────────────────
    df_c  = r["df_capex"]
    rows  = []
    for _, row in df_c.iterrows():
        qty_label = f"×1 (shared)" if row["Scale"] == "site" else f"×{int(row['Qty'])}"
        saving    = f"−{fmt_usd(row['Saving'])}" if row["Saving"] > 0 else ""
        cat_color = (C["amber"] if row["Category"] == "Site Fixed"
                     else C["blue"] if row["Category"] == "Process Core"
                     else C["text2"])
        rows.append(html.Tr([
            html.Td(row["Category"],  style={"color":cat_color,"fontSize":"10px","letterSpacing":"0.5px"}),
            html.Td(row["Equipment"], style={"fontSize":"11px"}),
            html.Td(fmt_usd(row["Unit ($)"]), style={"color":C["text2"],"fontSize":"11px"}),
            html.Td(qty_label,        style={"color":C["text2"],"fontSize":"11px"}),
            html.Td(fmt_usd(row["Total"]),style={"color":C["green"],"fontWeight":"700","fontSize":"11px"}),
            html.Td(saving,           style={"color":C["amber"],"fontSize":"10px"}),
        ]))

    # Saving summary row
    if r["capex_site_save"] > 0:
        rows.append(html.Tr([
            html.Td("✓ Shared saving", colSpan=4,
                    style={"color":C["green"],"fontSize":"10px","paddingTop":"8px"}),
            html.Td(f"−{fmt_usd(r['capex_site_save'])}",
                    style={"color":C["green"],"fontWeight":"700","fontSize":"11px","paddingTop":"8px"}),
            html.Td(""),
        ]))

    capex_tbl = html.Table([
        html.Thead(html.Tr([
            html.Th("Cat"),html.Th("Equipment"),html.Th("Unit"),
            html.Th("Qty"),html.Th("Total"),html.Th("Saving"),
        ], style={"fontSize":"9px","color":C["text2"],"borderBottom":f"1px solid {C['border']}"})),
        html.Tbody(rows),
    ], style={"width":"100%","borderCollapse":"collapse","fontFamily":"monospace"})

    # CAPEX summary table
    capex_sum_rows = [
        ("Process CAPEX",       fmt_usd(r["capex_process"])),
        ("CHP / Solar CAPEX",   fmt_usd(r["capex_chp"]) if r["capex_chp"] > 0 else "—"),
        ("TOTAL CAPEX",         fmt_usd(r["capex_total"])),
        ("Naive linear",        fmt_usd(r["capex_naive"]) if r["n_modules"] > 1 else "—"),
        ("Shared infra saving", f"−{fmt_usd(r['capex_site_save'])}" if r["capex_site_save"]>0 else "—"),
        ("CAPEX intensity",     f"${r['capex_intensity']:,.0f}/t·yr"),
    ]
    capex_sum = html.Table([
        html.Tbody([
            html.Tr([
                html.Td(k, style={"color":C["text2"],"fontSize":"11px","fontFamily":"monospace",
                                  "paddingBottom":"6px","paddingRight":"16px"}),
                html.Td(v, style={"color":C["green"],"fontSize":"11px","fontFamily":"monospace",
                                  "fontWeight":"700","textAlign":"right","paddingBottom":"6px"}),
            ]) for k, v in capex_sum_rows
        ])
    ], style={"width":"100%","borderCollapse":"collapse"})

    # CAPEX donut
    site_t   = df_c[df_c["Scale"]=="site"]["Total"].sum()
    torch_t  = df_c[df_c["Scale"]=="torch"]["Total"].sum()
    module_t = df_c[df_c["Scale"]=="module"]["Total"].sum()
    fig_donut = go.Figure(go.Pie(
        labels=["Site Fixed","Plasma Torches","Process Equipment"],
        values=[site_t, torch_t, module_t],
        hole=0.55,
        marker_colors=[C["amber"], C["blue"], C["green"]],
        textfont_size=10,
    ))
    fig_donut.update_layout(
        margin=dict(t=0,b=0,l=0,r=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"], showlegend=True,
        legend=dict(font_size=9, bgcolor="rgba(0,0,0,0)", orientation="h", y=-0.15),
    )

    capex_note = (
        f"Kraft mills benchmark: $1,200–$2,000/t·yr.  "
        f"Nanoweave: ${r['capex_intensity']:,.0f}/t·yr  "
        f"({'✅ competitive' if r['capex_intensity'] < 800 else '⚠️ above target at this scale'})."
    )

    # ── OPEX stacked bar ──────────────────────────────────────────────────
    opex_items = {
        "Energy":       r["energy_yr"],
        "NaOH":         r["naoh_yr"],
        "Labor":        r["labor_yr"],
        "Water":        r["water_yr"],
        "Logistics":    r["logistics_yr"],
        "Depreciation": r["depr_yr"],
    }
    opex_colors = [C["amber"], "#60a5fa", C["green"], "#34d399", "#f97316", "#a78bfa"]

    fig_opex = go.Figure()
    for (lbl, val), col in zip(opex_items.items(), opex_colors):
        fig_opex.add_trace(go.Bar(
            name=lbl, x=[val], y=["OPEX"], orientation="h",
            marker_color=col,
            text=fmt_usd(val) if val > 0 else "",
            textposition="inside", textfont_size=9,
        ))
    fig_opex.update_layout(
        barmode="stack", height=80,
        margin=dict(t=0,b=0,l=0,r=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"], showlegend=True,
        legend=dict(orientation="h",font_size=9,bgcolor="rgba(0,0,0,0)",y=-1.6),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )

    # OPEX table
    opex_tbl_rows = [
        html.Tr([
            html.Td(k, style={"fontSize":"11px","fontFamily":"monospace","color":C["text2"],"paddingBottom":"5px","paddingRight":"16px"}),
            html.Td(f"${v/r['total_cell_yr']:,.0f}" if r["total_cell_yr"]>0 else "—",
                    style={"fontSize":"11px","fontFamily":"monospace","color":C["text"],"textAlign":"right","paddingRight":"16px","paddingBottom":"5px"}),
            html.Td(fmt_usd(v),
                    style={"fontSize":"11px","fontFamily":"monospace","color":C["green"],"fontWeight":"700","textAlign":"right","paddingBottom":"5px"}),
        ])
        for k, v in opex_items.items()
    ] + [html.Tr([
        html.Td("TOTAL OPEX",style={"fontSize":"11px","fontFamily":"monospace","color":C["text"],"fontWeight":"700","borderTop":f"1px solid {C['border']}","paddingTop":"6px","paddingRight":"16px"}),
        html.Td(f"${r['opex_per_t']:,.0f}",style={"fontSize":"11px","fontFamily":"monospace","color":C["green"],"fontWeight":"700","textAlign":"right","paddingRight":"16px","borderTop":f"1px solid {C['border']}","paddingTop":"6px"}),
        html.Td(fmt_usd(r["total_opex"]),style={"fontSize":"11px","fontFamily":"monospace","color":C["green"],"fontWeight":"700","textAlign":"right","borderTop":f"1px solid {C['border']}","paddingTop":"6px"}),
    ])]

    opex_tbl = html.Table([
        html.Thead(html.Tr([
            html.Th("Cost Item",style={"fontSize":"9px","color":C["text2"],"fontFamily":"monospace","textAlign":"left","paddingBottom":"4px","paddingRight":"16px","borderBottom":f"1px solid {C['border']}"}),
            html.Th("$/ton",    style={"fontSize":"9px","color":C["text2"],"fontFamily":"monospace","textAlign":"right","paddingBottom":"4px","paddingRight":"16px","borderBottom":f"1px solid {C['border']}"}),
            html.Th("$/yr",     style={"fontSize":"9px","color":C["text2"],"fontFamily":"monospace","textAlign":"right","paddingBottom":"4px","borderBottom":f"1px solid {C['border']}"}),
        ])),
        html.Tbody(opex_tbl_rows),
    ], style={"width":"100%","borderCollapse":"collapse","marginTop":"10px"})

    # ── Revenue waterfall ─────────────────────────────────────────────────
    margin_color = C["green"] if r["margin"] >= 0 else C["red"]
    fig_wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute","absolute","total",
                 "relative","relative","relative","relative","relative","relative","total"],
        x=["Cellulose","Org. Fertilizer","Revenue",
           "−Energy","−NaOH","−Labor","−Water","−Logistics","−Deprec.","EBITDA"],
        y=[r["rev_cell_mid"], r["rev_fert"], 0,
           -r["energy_yr"], -r["naoh_yr"], -r["labor_yr"],
           -r["water_yr"],  -r["logistics_yr"], -r["depr_yr"], 0],
        connector_line=dict(color=C["border"]),
        increasing_marker_color=C["green"],
        decreasing_marker_color=C["red"],
        totals_marker_color=C["blue"],
        texttemplate="%{y:$,.0f}",
        textfont_size=9,
    ))
    fig_wf.update_layout(
        height=300, margin=dict(t=20,b=20,l=0,r=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"], showlegend=False,
        xaxis=dict(tickfont_size=9),
        yaxis=dict(tickprefix="$",tickformat=",.0f",tickfont_size=9,gridcolor=C["border"]),
    )

    payback_str = (f"{r['payback']:.1f} years" if r["payback"] else "n/a — negative margin")
    ebitda_note = [
        html.Strong(f"EBITDA: {fmt_usd(r['margin'])}",
                    style={"color": margin_color}),
        f"  |  {r['margin_pct']:.1f}% margin  |  "
        f"Payback: {payback_str}  |  "
        f"CAPEX intensity: ${r['capex_intensity']:,.0f}/t·yr",
    ]

    # ── Environmental KPIs ────────────────────────────────────────────────
    co2_val  = f"{r['co2_avoided']/1000:.1f}K t/yr"
    co2_sub  = "from biomass diversion"
    tree_val = f"{r['trees_saved']:,}/yr"
    tree_sub = "from deforestation"
    ha_val   = f"{r['hectares']:,} ha/yr"
    ha_sub   = "forest saved"
    n_val    = f"{r['total_fert_yr']:,.0f} t/yr"
    n_sub    = f"org. fertilizer · {r['total_n_yr']:,.0f} t N equiv."
    cell_val = f"{r['total_cell_yr']/1000:.1f}K t/yr"
    cell_sub = "Kappa ≤ 2 bleached"
    tot_val  = f"{r['co2_total']/1000:.1f}K t/yr"
    tot_sub  = "avoided + mitigated"

    # ── Sensitivity table ─────────────────────────────────────────────────
    SITE_FIXED = sum(uc*qpm for _,uc,qpm,sc,_,chp_only in EQUIPMENT
                     if sc == "site")
    CAPEX_VAR  = sum(uc*qpm for _,uc,qpm,sc,_,chp_only in EQUIPMENT
                     if sc != "site" and not (chp_only and power_mode == "grid"))
    dm = 1 - bd["moisture"]

    cell_prices = [600, 900, 1200, 1600]
    scales      = [50, 100, 200, 500, 1000]

    header_row = html.Tr([
        html.Th("Scale",     style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","padding":"6px 10px","textAlign":"left","borderBottom":f"1px solid {C['border']}"}),
        html.Th("Modules",   style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","padding":"6px 10px","textAlign":"right","borderBottom":f"1px solid {C['border']}"}),
        html.Th("CAPEX",     style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","padding":"6px 10px","textAlign":"right","borderBottom":f"1px solid {C['border']}"}),
        html.Th("$/t·yr",    style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","padding":"6px 10px","textAlign":"right","borderBottom":f"1px solid {C['border']}"}),
    ] + [
        html.Th(f"${p}/t margin", style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","padding":"6px 10px","textAlign":"right","borderBottom":f"1px solid {C['border']}"})
        for p in cell_prices
    ])

    body_rows = []
    for scale in scales:
        nm        = max(1, math.ceil(scale / 50))
        cy        = scale * dm * bd["cellulose_dm"] * PROC_EFFICIENCY * OP_DAYS
        ny        = scale * dm * bd["n_fixation"] * OP_DAYS
        capex_s   = SITE_FIXED + nm * CAPEX_VAR
        ci_s      = capex_s / cy if cy > 0 else 0
        energy_s  = 0 if power_mode == "chp" else nm * TOTAL_ENERGY_MODULE * OP_DAYS * elec_price
        opex_s    = (energy_s + nm*NAOH_T_PER_MOD*NAOH_PRICE*OP_DAYS
                     + nm*LABOR_PER_MODULE + nm*WATER_PER_MOD*OP_DAYS + capex_s/depr_years)
        ci_color  = C["green"] if ci_s < 600 else C["text"] if ci_s < 1200 else C["red"]
        margin_cells = []
        for p in cell_prices:
            m   = cy*p + ny*fert_price - opex_s
            col = C["green"] if m > 0 else C["red"]
            margin_cells.append(
                html.Td(fmt_usd(m), style={"fontSize":"11px","fontFamily":"monospace",
                                           "color":col,"fontWeight":"700",
                                           "textAlign":"right","padding":"7px 10px",
                                           "borderBottom":f"1px solid rgba(30,46,36,0.4)"})
            )
        body_rows.append(html.Tr([
            html.Td(f"{scale} TPD",   style={"fontSize":"11px","fontFamily":"monospace","color":C["text2"],"padding":"7px 10px","borderBottom":f"1px solid rgba(30,46,36,0.4)"}),
            html.Td(str(nm),          style={"fontSize":"11px","fontFamily":"monospace","color":C["text2"],"textAlign":"right","padding":"7px 10px","borderBottom":f"1px solid rgba(30,46,36,0.4)"}),
            html.Td(fmt_usd(capex_s), style={"fontSize":"11px","fontFamily":"monospace","color":C["text"],"textAlign":"right","padding":"7px 10px","borderBottom":f"1px solid rgba(30,46,36,0.4)"}),
            html.Td(f"${ci_s:,.0f}",  style={"fontSize":"11px","fontFamily":"monospace","color":ci_color,"fontWeight":"700","textAlign":"right","padding":"7px 10px","borderBottom":f"1px solid rgba(30,46,36,0.4)"}),
        ] + margin_cells))

    sens_tbl = html.Div([
        html.Div("Green = positive margin · Red = negative · CAPEX $/t·yr vs Kraft benchmark $1,200–$2,000",
                 style={"fontSize":"10px","color":C["text2"],"fontFamily":"monospace","marginBottom":"8px"}),
        html.Table([html.Thead(header_row), html.Tbody(body_rows)],
                   style={"width":"100%","borderCollapse":"collapse","fontFamily":"monospace"}),
    ], style={**CARD_STYLE,"overflowX":"auto"})

    # ── Energy bar chart ──────────────────────────────────────────────────
    fig_en = go.Figure(go.Bar(
        x=list(ENERGY_KWH_DAY.keys()),
        y=list(ENERGY_KWH_DAY.values()),
        marker_color=[C["green"], C["blue"], C["amber"]],
        text=[f"{v:,}" for v in ENERGY_KWH_DAY.values()],
        textposition="outside", textfont_size=10,
    ))
    fig_en.add_hline(
        y=TOTAL_ENERGY_MODULE, line_dash="dot", line_color=C["red"],
        annotation_text=f"Total: {TOTAL_ENERGY_MODULE:,} kWh/day",
        annotation_font_size=10, annotation_font_color=C["red"],
    )
    fig_en.update_layout(
        height=240, margin=dict(t=30,b=10,l=0,r=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"], showlegend=False,
        yaxis=dict(ticksuffix=" kWh", gridcolor=C["border"], tickfont_size=10),
        xaxis=dict(tickfont_size=10),
    )

    # ── CHP balance ───────────────────────────────────────────────────────
    if power_mode == "chp":
        chp_rows = [
            ("CHP biomass/day",    f"{r['biomass_chp']:.1f} t/day"),
            ("Electricity gen.",   f"{r['biomass_chp']*r['elec_kwh_per_t_wet']:,.0f} kWh/day"),
            ("Heat generated",     f"{r['heat_generated']:,.0f} kWh/day"),
            ("Heat for drying",    f"~{r['heat_for_drying']:,.0f} kWh/day"),
            ("Heat surplus",       f"~{r['heat_surplus']:,.0f} kWh/day"),
            ("Grid cost",          "$0 — self-powered"),
        ]
    elif power_mode == "hybrid":
        chp_rows = [
            ("CHP biomass/day",    f"{r['biomass_chp']:.1f} t/day"),
            ("CHP covers",         "50% of electricity"),
            ("Grid electricity",   f"${elec_price:.3f}/kWh"),
            ("Effective cost",     f"${r['elec_cost']:.3f}/kWh"),
            ("Annual energy cost", fmt_usd(r["energy_yr"])),
        ]
    else:
        chp_rows = [
            ("Power mode",         "Grid Only"),
            ("Grid price",         f"${elec_price:.3f}/kWh"),
            ("Annual energy cost", fmt_usd(r["energy_yr"])),
            ("Energy per module",  f"{TOTAL_ENERGY_MODULE:,} kWh/day"),
            ("Tip",                "Switch to CHP to save " + fmt_usd(r["energy_yr"])),
        ]

    chp_balance = html.Div([
        html.Div([
            html.Div(k, style={"color":C["text2"],"fontSize":"12px","fontFamily":"monospace"}),
            html.Div(v, style={"color":C["green"],"fontSize":"12px","fontFamily":"monospace","fontWeight":"700"}),
        ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
                  "borderBottom":f"1px solid {C['border']}","paddingBottom":"7px","marginBottom":"7px"})
        for k, v in chp_rows
    ])

    # ── Return all outputs ────────────────────────────────────────────────
    return (
        biomass_disp, elec_grp_style,
        str(r["n_modules"]), f"{r['n_modules']*50} TPD capacity",
        str(r["n_modules"]), f"{r['n_modules']*100} kW installed",
        f"{r['total_cell_day']:.1f} t/day", f"{r['total_cell_yr']:,.0f} t/yr",
        chp_val, chp_sub,
        feed_note,
        capex_tbl, capex_sum, fig_donut, capex_note,
        fig_opex, opex_tbl,
        fig_wf, ebitda_note,
        co2_val, co2_sub, tree_val, tree_sub, ha_val, ha_sub,
        n_val, n_sub, cell_val, cell_sub, tot_val, tot_sub,
        sens_tbl,
        fig_en, chp_balance,
    )


# ══════════════════════════════════════════════════════════════════════════════
# FRANCHISE MODEL CALLBACK
# ══════════════════════════════════════════════════════════════════════════════

@app.callback(
    # KPIs
    Output("fr-kpi-modules",       "children"),
    Output("fr-kpi-modules-sub",   "children"),
    Output("fr-kpi-cellulose",     "children"),
    Output("fr-kpi-cellulose-sub", "children"),
    Output("fr-kpi-license",       "children"),
    Output("fr-kpi-license-sub",   "children"),
    Output("fr-kpi-margin",        "children"),
    Output("fr-kpi-margin-sub",    "children"),
    Output("fr-kpi-bep",           "children"),
    Output("fr-kpi-bep-sub",       "children"),
    # Charts
    Output("fr-cashflow-chart",    "figure"),
    Output("fr-annual-bar",        "figure"),
    Output("fr-price-table",       "children"),

    # Franchise inputs
    Input("fr-franchisees",   "value"),
    Input("fr-modules",       "value"),
    Input("fr-lifespan",      "value"),
    Input("fr-rampup",        "value"),
    Input("fr-hq-opex",       "value"),
    Input("fr-sell-price",    "value"),
    Input("fr-dev-capex",     "value"),
    # Pass-through from main TEA inputs (so franchise model stays in sync)
    Input("biomass-slider",   "value"),
    Input("biomass-type",     "value"),
    Input("power-mode",       "value"),
    Input("elec-price",       "value"),
    Input("fert-price",       "value"),
    Input("depr-years",       "value"),
    Input("logistics-cost",   "value"),
)
def update_franchise(
    n_franchisees, modules_per_fr, lifespan, rampup_months,
    hq_opex, sell_price, dev_capex,
    biomass_total, biomass_key, power_mode, elec_price, fert_price, depr_years,
    logistics_cost,
):
    # ── Safety defaults ────────────────────────────────────────────────────
    n_franchisees  = n_franchisees  or 5
    modules_per_fr = modules_per_fr or 8
    lifespan       = lifespan       or 15
    rampup_months  = rampup_months  or 12
    hq_opex        = hq_opex        or 800_000
    sell_price     = sell_price     or 900
    dev_capex      = dev_capex      or 2_000_000
    elec_price     = elec_price     or 0.08
    fert_price     = fert_price     or 350
    depr_years     = depr_years     or 15

    LICENSE_PCT    = 0.05   # 5% of franchisee EBITDA — fixed per franchise model

    # ── Pull per-module economics from TEA engine ──────────────────────────
    logistics_cost = logistics_cost or 6.0
    r1 = run_calculations(
        50, biomass_key, power_mode, elec_price,
        sell_price, fert_price, depr_years, logistics_cost,
    )

    cell_yr_per_mod = r1["total_cell_yr"]   # t cellulose/yr per module

    # ── Franchisee CAPEX → converted to annual lease (OPEX) ──────────────
    # The franchisee does NOT buy equipment outright. They lease it.
    # The lease payment = CAPEX ÷ lifespan (straight-line, no interest for
    # simplicity — full lease cost recovered over the project lifetime).
    # This lease payment IS an annual OPEX line for the franchisee.
    #
    # Total franchisee OPEX = cash OPEX (energy + NaOH + labour + water +
    #                          logistics) + annual lease payment
    #
    # Buy-price basis = total OPEX per ton (including lease)
    # so the franchisee covers ALL costs including lease from the buy price.
    #
    # EBITDA = revenue − total OPEX (including lease)
    # License (5%) is levied on this EBITDA.
    #
    # Developer cash flow is unchanged — they pay buy_price, receive sell_price
    # + license. Developer CAPEX = own HQ/logistics only.

    fr_capex_per_mod   = r1["capex_total"]
    fr_lease_yr        = fr_capex_per_mod / lifespan    # annual lease payment
    fr_cash_opex_yr    = (r1["energy_yr"] + r1["naoh_yr"] +
                          r1["labor_yr"] + r1["water_yr"] +
                          r1["logistics_yr"])           # pure cash costs
    fr_total_opex_yr   = fr_cash_opex_yr + fr_lease_yr  # total incl. lease
    total_opex_per_t   = fr_total_opex_yr / cell_yr_per_mod if cell_yr_per_mod > 0 else 0

    # ── Franchise model P&L ────────────────────────────────────────────────
    # buy_price = total_opex_per_t × (1 + margin)
    # Franchisee EBITDA = revenue − total OPEX (incl. lease)
    # License = 5% × franchisee EBITDA
    # Developer trade margin = (sell_price − buy_price) × cell_yr
    # Developer net/mod = trade margin + license
    scenarios = {
        "+10%": {"margin_pct": 0.10, "color": C["green"],  "dash": "solid"},
        "+15%": {"margin_pct": 0.15, "color": C["blue"],   "dash": "dash"},
        "+20%": {"margin_pct": 0.20, "color": C["amber"],  "dash": "dot"},
    }

    for label, s in scenarios.items():
        mp = s["margin_pct"]
        buy_price_per_t = total_opex_per_t * (1 + mp)
        s["buy_price_per_t"] = buy_price_per_t

        # Franchisee P&L (per module/yr)
        fr_revenue_per_mod = buy_price_per_t * cell_yr_per_mod
        fr_ebitda_per_mod  = fr_revenue_per_mod - fr_total_opex_yr  # after lease
        fr_margin_pct      = (fr_ebitda_per_mod / fr_revenue_per_mod * 100
                              if fr_revenue_per_mod > 0 else 0)
        license_per_mod    = max(0, fr_ebitda_per_mod * LICENSE_PCT)

        # Developer P&L (per module/yr)
        dev_trade_per_mod  = (sell_price - buy_price_per_t) * cell_yr_per_mod
        dev_income_per_mod = dev_trade_per_mod + license_per_mod

        # Portfolio totals at maturity
        tot_mods        = n_franchisees * modules_per_fr
        fr_total_rev    = tot_mods * fr_revenue_per_mod
        fr_total_opex   = tot_mods * fr_total_opex_yr
        fr_total_ebitda = tot_mods * fr_ebitda_per_mod
        dev_total_trade = tot_mods * dev_trade_per_mod
        dev_total_lic   = tot_mods * license_per_mod
        dev_gross       = dev_total_trade + dev_total_lic

        s["fr_revenue_per_mod"]       = fr_revenue_per_mod
        s["fr_ebitda_per_mod"]        = fr_ebitda_per_mod
        s["fr_margin_pct"]            = fr_margin_pct
        s["license_per_mod"]          = license_per_mod
        s["dev_trade_margin_per_mod"] = dev_trade_per_mod
        s["dev_income_per_mod"]       = dev_income_per_mod
        s["fr_total_rev"]             = fr_total_rev
        s["fr_total_opex"]            = fr_total_opex
        s["fr_total_ebitda"]          = fr_total_ebitda
        s["dev_total_trade"]          = dev_total_trade
        s["dev_total_lic"]            = dev_total_lic
        s["dev_gross"]                = dev_gross

    # ── Year-by-year roll-out model ────────────────────────────────────────
    # Franchisees come online one at a time, each taking rampup_months
    # Each franchisee operates modules_per_fr modules
    # Once online: full modules in production
    years = list(range(1, lifespan + 1))

    # When does each franchisee reach full production? (in fractional years)
    fr_online_year = [(i * rampup_months / 12) for i in range(1, n_franchisees + 1)]

    def active_modules_in_year(yr):
        """How many total modules are fully active in a given year?"""
        total = 0
        for online_yr in fr_online_year:
            if yr > online_yr:
                total += modules_per_fr
            elif yr == math.ceil(online_yr):
                # Partial year — pro-rate
                partial = (yr - online_yr) if online_yr < yr else 0
                total += modules_per_fr * max(0, min(1, partial))
        return total

    # Build annual cash flows for each scenario
    cf_data = {}
    for label, s in scenarios.items():
        annual_net = []
        for yr in years:
            mods = active_modules_in_year(yr)
            dev_income = mods * s["dev_income_per_mod"]
            net = dev_income - hq_opex - (dev_capex / depr_years)
            annual_net.append(net)
        # Cumulative including initial CAPEX
        cumulative = []
        running = -dev_capex
        for net in annual_net:
            running += net
            cumulative.append(running)
        cf_data[label] = {"annual": annual_net, "cumulative": cumulative}

    # ── Break-even year (mid scenario = +15%) ─────────────────────────────
    mid_cum = cf_data["+15%"]["cumulative"]
    bep_year = None
    for i, cum in enumerate(mid_cum):
        if cum >= 0:
            bep_year = years[i]
            break

    # ── Total modules & cellulose at maturity ─────────────────────────────
    total_mods_mature   = n_franchisees * modules_per_fr
    total_cell_mature   = total_mods_mature * cell_yr_per_mod
    license_mature_mid  = total_mods_mature * scenarios["+15%"]["license_per_mod"]
    margin_mature_mid   = cf_data["+15%"]["annual"][-1]

    # ── KPIs ──────────────────────────────────────────────────────────────
    kpi_mods      = f"{total_mods_mature}"
    kpi_mods_sub  = f"{n_franchisees} franchisees × {modules_per_fr} modules"
    kpi_cell      = fmt_usd(total_cell_mature, 0).replace("$","") + " t/yr"
    kpi_cell_sub  = f"{total_mods_mature} modules · {OP_DAYS} op. days"
    kpi_lic       = fmt_usd(license_mature_mid)
    kpi_lic_sub   = f"5% × franchisee EBITDA · {total_mods_mature} modules"
    kpi_mar       = fmt_usd(margin_mature_mid)
    kpi_mar_sub   = f"Net after HQ OPEX {fmt_usd(hq_opex)}/yr · mid scenario"
    kpi_bep       = f"Year {bep_year}" if bep_year else "Beyond lifespan"
    kpi_bep_sub   = "Mid scenario (+15%) · cumulative CF = 0"

    # ── Cumulative cash flow chart ─────────────────────────────────────────
    fig_cf = go.Figure()

    # Zero line
    fig_cf.add_hline(y=0, line_color=C["text2"], line_dash="dot", line_width=1)

    # Initial CAPEX marker
    fig_cf.add_annotation(
        x=0, y=-dev_capex,
        text=f"Dev. CAPEX: {fmt_usd(dev_capex)}",
        showarrow=False, font=dict(size=9, color=C["red"]),
        xanchor="left", yanchor="bottom",
    )

    for label, s in scenarios.items():
        cum = [-dev_capex] + cf_data[label]["cumulative"]
        x_vals = [0] + years
        fig_cf.add_trace(go.Scatter(
            x=x_vals, y=cum,
            name=f"Buy price {label} (${s['buy_price_per_t']:,.0f}/t)",
            mode="lines+markers",
            line=dict(color=s["color"], width=2.5, dash=s["dash"]),
            marker=dict(size=5),
            hovertemplate="Year %{x}<br>Cumulative: $%{y:,.0f}<extra></extra>",
        ))

    # Break-even annotation
    if bep_year:
        bep_val = cf_data["+15%"]["cumulative"][bep_year - 1]
        fig_cf.add_annotation(
            x=bep_year, y=bep_val,
            text=f"Break-even Yr {bep_year}",
            showarrow=True, arrowhead=2, arrowcolor=C["blue"],
            font=dict(size=9, color=C["blue"]),
            bgcolor=C["surface2"], bordercolor=C["blue"],
        )

    # Franchisee ramp-up markers
    for i, oy in enumerate(fr_online_year):
        if oy <= lifespan:
            fig_cf.add_vline(
                x=oy, line_dash="dot", line_color=C["text2"], line_width=0.8,
                annotation_text=f"Fr.{i+1}", annotation_font_size=8,
                annotation_font_color=C["text2"],
            )

    fig_cf.update_layout(
        height=400, margin=dict(t=20,b=40,l=10,r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"],
        legend=dict(font_size=10, bgcolor="rgba(0,0,0,0)",
                    orientation="h", y=-0.18),
        xaxis=dict(title="Year", tickfont_size=10, gridcolor=C["border"],
                   tick0=0, dtick=1),
        yaxis=dict(title="Cumulative Cash Flow ($)", tickformat="$,.0f",
                   tickfont_size=10, gridcolor=C["border"]),
        hovermode="x unified",
    )

    # ── Annual cash flow bar (mid scenario) ───────────────────────────────
    annual_mid = cf_data["+15%"]["annual"]
    bar_colors = [C["green"] if v >= 0 else C["red"] for v in annual_mid]

    fig_bar = go.Figure(go.Bar(
        x=years, y=annual_mid,
        marker_color=bar_colors,
        text=[fmt_usd(v) for v in annual_mid],
        textposition="outside", textfont_size=9,
        hovertemplate="Year %{x}<br>Net: $%{y:,.0f}<extra></extra>",
    ))
    fig_bar.add_hline(y=0, line_color=C["text2"], line_dash="dot", line_width=1)
    fig_bar.update_layout(
        height=260, margin=dict(t=20,b=20,l=0,r=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=C["text2"], showlegend=False,
        xaxis=dict(title="Year", tickfont_size=9, gridcolor=C["border"]),
        yaxis=dict(tickformat="$,.0f", tickfont_size=9, gridcolor=C["border"]),
    )

    # ── Full P&L scenario table ───────────────────────────────────────────
    TH = {"fontSize":"9px","color":C["text2"],"fontFamily":"monospace",
          "padding":"5px 8px","borderBottom":f"1px solid {C['border']}",
          "textAlign":"left","letterSpacing":"0.8px","whiteSpace":"nowrap"}
    TD = lambda v,col=C["text"]: {"fontSize":"10px","fontFamily":"monospace",
          "color":col,"padding":"5px 8px",
          "borderBottom":f"1px solid rgba(30,46,36,0.3)"}
    TDB = lambda v,col=C["text"]: {"fontSize":"10px","fontFamily":"monospace",
          "color":col,"padding":"5px 8px","fontWeight":"700",
          "borderBottom":f"1px solid rgba(30,46,36,0.3)"}

    sc_cols = {"+10%": C["green"], "+15%": C["blue"], "+20%": C["amber"]}

    # Header rows — two levels
    h1 = html.Tr([
        html.Th("", style={**TH,"borderRight":f"2px solid {C['border']}"}),
        html.Th("BUY PRICE", style={**TH,"textAlign":"center"}),
        html.Th("─── FRANCHISEE P&L (per module/yr) ───",
                colSpan=4, style={**TH,"textAlign":"center",
                "borderLeft":f"1px solid {C['border']}",
                "borderRight":f"2px solid {C['border']}","color":C["green"]}),
        html.Th("─── DEVELOPER P&L (per module/yr) ───",
                colSpan=3, style={**TH,"textAlign":"center","color":C["blue"]}),
    ])
    h2 = html.Tr([
        html.Th("Scenario",         style=TH),
        html.Th("$/t cellulose",    style=TH),
        html.Th("Revenue",          style={**TH,"borderLeft":f"1px solid {C['border']}"}),
        html.Th("OPEX+Lease",       style=TH),
        html.Th("EBITDA",           style=TH),
        html.Th("Margin %",         style={**TH,"borderRight":f"2px solid {C['border']}"}),
        html.Th("Trade Margin",     style=TH),
        html.Th("License (5%)",     style=TH),
        html.Th("Net Income",       style={**TH,"fontWeight":"700"}),
    ])

    data_rows = []
    for label, s in scenarios.items():
        sc = sc_cols[label]
        ebitda_col = C["green"] if s["fr_ebitda_per_mod"] >= 0 else C["red"]
        data_rows.append(html.Tr([
            html.Td(label,                                 style={**TDB(0,sc)}),
            html.Td(f"${s['buy_price_per_t']:,.0f}",      style=TD(0)),
            html.Td(fmt_usd(s["fr_revenue_per_mod"]),     style={**TD(0),"borderLeft":f"1px solid {C['border']}"}),
            html.Td(fmt_usd(fr_total_opex_yr),            style=TD(0)),
            html.Td(fmt_usd(s["fr_ebitda_per_mod"]),      style={**TD(0), "color": ebitda_col}),
            html.Td(f"{s['fr_margin_pct']:.1f}%",         style={**TD(0),"borderRight":f"2px solid {C['border']}"}),
            html.Td(fmt_usd(s["dev_trade_margin_per_mod"]),style={**TD(0,C["blue"])}),
            html.Td(fmt_usd(s["license_per_mod"]),        style=TD(0, C["amber"])),
            html.Td(fmt_usd(s["dev_income_per_mod"]),     style=TDB(0)),
        ]))

    # Portfolio totals at maturity (mid scenario)
    mid = scenarios["+15%"]
    data_rows.append(html.Tr([
        html.Td(f"Portfolio mature ({n_franchisees}×{modules_per_fr} mod)",
                colSpan=1, style={**TD(0,C["text2"]),"fontStyle":"italic","fontSize":"9px"}),
        html.Td("—",                                   style=TD(0,C["text2"])),
        html.Td(fmt_usd(mid["fr_total_rev"]),          style={**TDB(0,C["green"]),"borderLeft":f"1px solid {C['border']}"}),
        html.Td(fmt_usd(mid["fr_total_opex"]),         style=TDB(0)),
        html.Td(fmt_usd(mid["fr_total_ebitda"]),       style=TDB(0,C["green"])),
        html.Td("—",                                   style={**TD(0,C["text2"]),"borderRight":f"2px solid {C['border']}"}),
        html.Td(fmt_usd(mid["dev_total_trade"]),       style=TDB(0,C["blue"])),
        html.Td(fmt_usd(mid["dev_total_lic"]),         style=TDB(0,C["amber"])),
        html.Td(fmt_usd(margin_mature_mid),
                style={**TDB(0, C["green"] if margin_mature_mid>0 else C["red"])}),
    ]))

    price_tbl = html.Div([
        html.Div([
            html.Span(f"Market sell: ", style={"color":C["text2"]}),
            html.Span(f"${sell_price}/t", style={"color":C["amber"],"fontWeight":"700"}),
            html.Span(f"  ·  Total OPEX/t (incl. lease): ", style={"color":C["text2"]}),
            html.Span(fmt_usd(total_opex_per_t)+"/t", style={"color":C["green"],"fontWeight":"700"}),
            html.Span(f"  ·  of which lease: ", style={"color":C["text2"]}),
            html.Span(fmt_usd(fr_lease_yr)+"/yr/mod", style={"color":C["blue"],"fontWeight":"700"}),
            html.Span(f"  (CAPEX {fmt_usd(fr_capex_per_mod)} ÷ {lifespan} yrs)",
                      style={"color":C["text2"]}),
            html.Span(f"  ·  License: 5% franchisee EBITDA (after lease)",
                      style={"color":C["text2"]}),
        ], style={"fontSize":"10px","fontFamily":"monospace","marginBottom":"8px"}),
        html.Table(
            [html.Thead([h1, h2]), html.Tbody(data_rows)],
            style={"width":"100%","borderCollapse":"collapse","fontFamily":"monospace"},
        ),
    ])

    return (
        kpi_mods, kpi_mods_sub,
        kpi_cell, kpi_cell_sub,
        kpi_lic,  kpi_lic_sub,
        kpi_mar,  kpi_mar_sub,
        kpi_bep,  kpi_bep_sub,
        fig_cf, fig_bar, price_tbl,
    )




# ══════════════════════════════════════════════════════════════════════════════
# PDF REPORT — NANOWEAVE BRAND COLOURS + EMBEDDED LOGO
# ══════════════════════════════════════════════════════════════════════════════

import io as _io
import base64 as _b64
from datetime import datetime as _dt
from reportlab.lib.pagesizes import letter as _letter
from reportlab.lib import colors as _colors
from reportlab.lib.units import inch as _inch
from reportlab.lib.styles import ParagraphStyle as _PS
from reportlab.lib.enums import TA_CENTER as _TAC, TA_LEFT as _TAL
from reportlab.platypus import (
    SimpleDocTemplate as _Doc, Paragraph as _P, Spacer as _Sp,
    Table as _T, TableStyle as _TS, PageBreak as _PB, HRFlowable as _HR,
)
from reportlab.platypus import Image as _RLImg

# ── Embedded logo (base64, extracted from official pitch deck) ─────────────
_LOGO_COVER = "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cM1pPwcxxtWIJCNUHclS4VEVhfaP4F6ICa4ZoCiJLO9glhnmTWpQn70GFcEFDzZRTf+IXYOIGhn5DLDAUNXcqVi0zihAuKdf9dFHD7D66KOPT0EkC3h1mZCVYjFJTqCZDJ8KptGkXXLlcfmRJykDPO/70y49aoolgSSI5O5GMwGUJAaawcbu5muMZAYimfCfEqRErTVEMEtFTDRtyzzt1vff+b519ySh0eVORs1NDClGtKTSrJRJd8v7sI8++ugBVh999PEhQtVINiJJFvQ0pJSYLIOr4uP3cZPcQdFY47KTIEgGNrlLMIunBkkIxm5VxEwSY7+Vv3/JlmVYqUyoaDlblCR+bLfDyJy0KuXl12IuA6p4sFiAVZti7EVG++ijB1h99NHHJx6SXFY/n+Jrd2Uv2f73Pu4uwlZDyrJqZ+YdFXBlYqWLcHeFNYNrSJrLbgrEYhjdmjB32l2p/niBcLkXGQjmLFXOpvkCiCOG9NCqjz56gNVHH318KgCWKYZjuvCXuVZlQ7fWqe/jjQyiWsK27OBp5evZ/WsSfKpIZojZNs5Z91lG8Tl0H+v9SJLtehSgk2HQTrk9dc+oJ7j30UcPsProo49PPIKLHXixHWBjO6E8l+k+Nt+cwjsCimlzfkkxpsak01KH3ZFpEIRBMyyX0Xr62RS4a0unlmUaPsYuQpMIkn0XxRSSopaLmPkelayWpF1npfXRRx89wOqjjz6eF1RMpYYezQhZt+NnySd5RBJr8nN29/2MXfSRa5wGU7udwTKMqGHqmuwx96Nt2pNOBuuj+P7bsKaBoV3mqu2mtCkQ+nik10cfffQAq48++vgEQdZP9vee9O924/2OUP4xx6yipe01P/nad/f9Z6uz98Cqjz7+oUffA9xHH3300UcfffTRA6w++uijjz766KOPHmD10UcfffTRRx999ACrjz766KOPPvroo48eYPXRRx999NFHH330AKuPPvroo48++uijB1h99NFHH3300UcfffQAq48++uijjz766KMHWH300UcfffTRRx89wOqjjz766KOPPvroowdYffTRRx999NFHHz3A6qOPPvroo48++ugBVh999NFHH3300UcPsProo48++uijjz766AFWH3300UcfffTRRw+w+uijjz766KOPPnqA1UcfffTRRx999NFHD7D66KOPPvroo48+eoDVRx99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Y0+I2B02Crs/xHeCQi1o3XWh9VMSZVjR8PPC4Wx5CBVu+Bz9bJJWXQHYWfm4VYCyT2hpcKuwlq2esnN59q14V7yy2neo2M6qwp1WKZpYO3rv/u+L1fqU5HryMFTacrmp88KwooHh01Ck7OKwQni7H5rqKhKQcbUH07D6US/SCbNYi+1L7UE0e66zi8Mqg+GT524TcbZcg0qdHzysOgzbS8PKwmzlbXZ6crYqVYZNqmy7hyTZrtXF3SOU/BAId1ZN73Tr6u1I868DDKsms9DV+47sEuNusZ5kc7XeKrs6aW/23nCpDnn29PhshOTmzVmThzQUzVvJw3qv3fl2DatzeQhobl4cDtnVQnDLRsi1Qexsy62U2rS03o362Nf84u/wMyFZ2LMquve9Mvx0K8P9uPUXkdmfycPQ62yzgyTD6lquW4azfnaz9359/e3czzLcz+T7+lr8pNs/y9YF2n5x2MouH3tLzz+jTOcHRQFLfnMi1scCz06YKvP0sWfLaFe7gJ0vnTvDhPOuI1qzkHWrn/NZyLP7YHsvArMLRLlDwNpzT82GVRjbG9p29+Mpd/+4+uRq64o9QW8W3moB/sdWoubbN3k4IcV8O+Tm2qSP36UCFndWfoojza5bVudE1kmN9TBAtrrqVGznds0Wie52Ck39P5UhxNmeoS47G8x2y8e7YLvC/CxYlr0PxRCw9mwDY3f1RNTX8fBczoeL7/rpxc1PSc7nTc5eqzbboq0Dnutqrxu2Qmtrg9qWoB4u2P0qu5vtqTIPOsPWedkJCXm+mmPDFx8btg133bZZjWPhrlf08q53a7FbvFfsk0by8LEX6PwzYrgtN+fxWU1YXd3KH7ufO3M/6zbzvFj/EwYszFethsat8y+Ds23p4fOlDM+JyKe6GqoPloiIiMhny+khEBEREVHAEhEREVHAEhEREVHAEhEREREFLBEREREFLBEREREFLBERERFRwBIRERFRwBIRERFRwBIRERERBSwRERERBSwRERERBSwRERERBSwRERERUcASERERUcASERERUcASEREREQUsEREREQUsEREREQUsEREREVHAEhEREVHAEhEREVHAEhEREREFLBEREREFLBEREREFLBEREREFLBERERFRwBIRERFRwBIRERFRwBIRERERBSwRERERBSwRERERBSwRERERUcASERERUcASERERUcASEREREQUsEREREQUsEREREQUsEREREQUsEREREVHAEhEREVHAEhEREVHAEhEREREFLBEREREFLBEREREFLBERERFRwBIRERFRwBIRERFRwBIRERERBSwRERERBSwRERERBSwRERERBSwRERERUcASERERUcASERERUcASEREREQUsEREREQUsEREREQUsEREREVHAEhEREVHAEhEREVHAEhEREVHAEhEREREFLBEREREFLBEREREFLBERERFRwBIRERFRwBIRERFRwBIRERERBSwRERERBSwRERERBSwRERERUcASERERUcASERERUcASERERUcASEREREQUsEREREQUsEREREQUsEREREVHAEhEREVHAEhEREVHAEhEREREFLBEREREFLBEREREFLBERERFRwBIRERFRwBIRERFRwBIRERFRwBIRERERBSwRERERBSwRERERBSwRERERUcASERERUcASERERUcASEREREQUsEREREQUsEREREQUsEREREVHAEhEREVHAEhEREVHAEhEREVHAEhEREREFLBEREZHfHP8XlPPrGiN/GrQAAAAASUVORK5CYII="
_LOGO_INTERIOR = "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+OKLKFWqFEKhkJRWM5LWdF3HwoULMWvWrCz3gQ42t912G/r06RN1LkKhEFRVxfLlyzFjxows35ekI0VR5FyWLFkSt9xyC9q1a4fmzZujRo0aMUkQO3bswPr167FkyRJ89913OHLkiEMqogNDpMOcfZMvVaoU3nzzTblO3GgrFEXBJ598gjVr1sQ875UrV8ZLL70EVVUl4UT67HvvvYdNmza53rh0XUcwGMTVV1+Np556KsNr299xGgNd1/H2229j69atAIAiRYrg/fffR9myZaNeJydAe8Bvv/2Gl156yfWhKZIUR/Nap04dPP300xmadCLNAb0f9P4nJydHJVuarxIlSuDVV19FUlJS1PeMrqdpGpYtW4bp06fHjbDoviVLlsTIkSOh67qrOaU5WLZsGWbMmAFFUXDFFVfgrbfegs/nk+snNxEKheD1ejFz5kxMmDAhboJaljUFedE8Ho9QVVUAEDVq1BCWZYlYcPDgQVG6dGmhKIrQNE0oihLT/XVdFwDE22+/HdN9hw4dKgCIhISEbD0/9feDDz4Q2cF9993neJ7szAcAUapUKXHy5MmY+nDhwgVRo0YNx3PF0jRNEwDE448/HtN9x48fn61np/sCENdee60YO3asOHToULr7mKYpDMMQwWBQGIYhWzAYlL8LhUIiFAo5vnf8+HHx6aefioYNGzruSWMdqamqKjwejyhUqJD4559/Yl4P77zzjqv7hL8H99xzj6vrv/rqq/J7sVz/iSeeiPlZQqGQqF69urxWqVKlxIkTJ0Re46effpJr3c0YZDYuI0eOzFZfHn74Ycd8ZNQfj8cjFEURuq6L9evXx3yfbdu2CU3THO9MdvYaus69996bpefu1KmTfNZmzZqJ/IAxY8bIdziveDVPvdztKqITJ07ANE0YhgHTNCO2tLQ0BINBpKamokKFCvjwww/lKTOrp/ULFy7ANE34/f4M70v3Nk0TqampcVFrWZaFAgUKoFOnTq7uH94CgQBM00SvXr3iquYJhUJITk523COjFgwGkZaWhgIFCuCTTz6R6uusgsbY7/fDMIwM1wJ97ty5c1led4qiwDRNVK1aVUq1/fv3R/ny5REKheS9SEuhKAo0TYvYyAYKwNHvUqVK4b///S9Wr16NyZMn44orrpCamIzGSQgBXddx/vx5zJ49G4ZhuFobNFd169aNSUKgdXPD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mjVr5noMr7/+erFs2bJ0sfSZxRiHQiHZt9GjRzviu+kZypUrJ86cOeMqzpbegR9//DFiTgZ77LSu62Lnzp2yH27ePerDhQsXRMWKFdPlP6C5pTmdPn161Dm1j49pmqJ58+aOd4auX7x4cbF3796oa5PGYNu2bTmaR8NtnD+Nh8fjEXfeeadcY5mtiVAolO79z2iO6POBQEBmJcxo34MtNwPCYtKjvXf2e91+++0x7YM0j4qiiG+//Va+k9H2GOrTkCFD0uX/oGdo2LChzIWR2RqmdTFjxgyRl7yHSzVTXE6oWIPBIK666iqMHDlSqpbyIu9zpNO5EEKeziN5/NrtZwCwc+dODB06VNqNMpOILctCYmIi7r777nyhjSCprHz58jIzVbjUlRNzoiiKlJJr1aqF//znP6497kkdqSgKhg4dinbt2mHt2rVSfRvuB0DPSU6Yq1atQtu2bfHyyy/LPoSXV81o3VqWhX79+qF69eoObYKqqjh27BhWrVolpSo3UtTVV1+NUqVKZfrZOnXqOOLDY6n2VaBAAanWDx8T6mdCQgKuueaadFqBSBImSfq7d++WueKzKolRf+KlaqdmzwzoVttkD0kUQuCee+6RJsLMJHNFUbB//3688MILOH78uHzHM5K6KSa9e/fuUdXhpP2hmPTFixc79ig3Ui6p92Mt0EM2eDchc7T+/X6/VLfHw0zp8/mgaZo0ocRrXeQHM+8lRejkwRkMBtG/f3907NgRfr8/amhGbhCcEALly5dHq1atMlS3k/qK+vrzzz9j165dWLt2bVRVDm0Od911FxISEvIsJj1cnWwYBrp374777rvPQaw56bhH1/7vf/+LhIQEOW7R7kd26/vvvx8jRoyA1+uV/TUMI92GSnNFHreqqsLn82H48OEYOHCg9PoPt1tH2vDJHPPwww87noHW7k8//eTqEETfK1asWETHNXtoU9OmTeHz+WJeKzQON910U7o+2dW9tWvXRrVq1aL2206Oq1evluFq2V2/Qoi4OsLZ0wHHOlamaaJMmTJo27ata9vxzz//jN27d+OXX36J+r7QWHXp0gUFCxZ0Nad0valTpzr2n2gHFABo164dChQo4HrtkGq9a9eujpwE0YowCSHw3XffYffu3VJFHg+eCE9vG491kR/Mu5cModuzBdGm9eGHH8rKTLmRPCSzUybwrzNMsWLF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"

# ── Brand palette ─────────────────────────────────────────────────────────
_DG  = _colors.HexColor("#1A4731")   # dark green
_MG  = _colors.HexColor("#2E7D52")   # mid green
_LG  = _colors.HexColor("#E8F5EE")   # light green
_GL  = _colors.HexColor("#3ddc84")   # green line
_AM  = _colors.HexColor("#B45309")   # amber
_AL  = _colors.HexColor("#FEF3C7")   # amber light
_SL  = _colors.HexColor("#374151")   # slate
_LGR = _colors.HexColor("#F3F4F6")   # light grey
_MGR = _colors.HexColor("#D1D5DB")   # mid grey
_BL  = _colors.HexColor("#0369A1")   # blue
_RD  = _colors.HexColor("#DC2626")   # red
_WH  = _colors.white


def _logo(w, h, which="cover"):
    """which: 'cover' = green logo, 'interior' = black logo on white"""
    b64 = _LOGO_COVER if which == "cover" else _LOGO_INTERIOR
    raw = _b64.b64decode(b64)
    return _RLImg(_io.BytesIO(raw), width=w*_inch, height=h*_inch)


def _ss():
    """Named paragraph styles."""
    return {
        "h1":   _PS("H1",  fontSize=13, leading=16, textColor=_DG,
                    fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=6),
        "h2":   _PS("H2",  fontSize=10, leading=13, textColor=_MG,
                    fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4),
        "body": _PS("BD",  fontSize=9,  leading=13, textColor=_SL,
                    fontName="Helvetica", spaceBefore=2, spaceAfter=2),
        "cap":  _PS("CP",  fontSize=8,  leading=11, textColor=_MGR,
                    fontName="Helvetica-Oblique", spaceBefore=2, spaceAfter=4),
        "kv":   _PS("KV",  fontSize=13, leading=17, textColor=_MG,
                    fontName="Helvetica-Bold", alignment=_TAC),
        "kl":   _PS("KL",  fontSize=7,  leading=9,  textColor=_SL,
                    fontName="Helvetica", alignment=_TAC),
        "ct":   _PS("CT",  fontSize=28, leading=34, textColor=_WH,
                    fontName="Helvetica-Bold", alignment=_TAC),
        "cs":   _PS("CS",  fontSize=13, leading=17,
                    textColor=_colors.HexColor("#A7F3D0"),
                    fontName="Helvetica", alignment=_TAC),
        "cm":   _PS("CM",  fontSize=9,  leading=12,
                    textColor=_colors.HexColor("#6EE7B7"),
                    fontName="Helvetica", alignment=_TAC),
    }


def _dt_row(data, cw, hbg=None, bold_last=0):
    hbg = hbg or _DG
    t = _T(data, colWidths=cw, repeatRows=1)
    cmds = [
        ("BACKGROUND",    (0,0),(-1,0), hbg),
        ("TEXTCOLOR",     (0,0),(-1,0), _WH),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,0), 8),
        ("ALIGN",         (0,0),(-1,0), "CENTER"),
        ("TOPPADDING",    (0,0),(-1,0), 6), ("BOTTOMPADDING",(0,0),(-1,0), 6),
        ("FONTNAME",      (0,1),(-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1),(-1,-1), 8),
        ("TEXTCOLOR",     (0,1),(-1,-1), _SL),
        ("ALIGN",         (1,1),(-1,-1), "RIGHT"),
        ("ALIGN",         (0,1),(0,-1),  "LEFT"),
        ("TOPPADDING",    (0,1),(-1,-1), 4), ("BOTTOMPADDING",(0,1),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 6), ("RIGHTPADDING", (0,0),(-1,-1), 6),
        ("LINEBELOW",     (0,0),(-1,0),  0.5, _MG),
        ("LINEBELOW",     (0,1),(-1,-2), 0.25, _MGR),
        ("BOX",           (0,0),(-1,-1), 0.5, _MGR),
    ]
    for i in range(1, len(data)):
        cmds.append(("BACKGROUND",(0,i),(-1,i), _LG if i%2==0 else _WH))
    for j in range(1, bold_last+1):
        ri = len(data)-j
        cmds += [("FONTNAME",(0,ri),(-1,ri),"Helvetica-Bold"),
                 ("BACKGROUND",(0,ri),(-1,ri),_LG),
                 ("LINEABOVE",(0,ri),(-1,ri),0.8,_MG)]
    t.setStyle(_TS(cmds))
    return t


def _kpi_block(pairs, cw, st):
    n = len(pairs)
    t = _T(
        [[_P(l, st["kl"]) for l,_ in pairs],
         [_P(v, st["kv"]) for _,v in pairs]],
        colWidths=[cw/n]*n,
    )
    t.setStyle(_TS([
        ("BACKGROUND",    (0,0),(-1,-1), _LG),
        ("BOX",           (0,0),(-1,-1), 0.5, _MG),
        ("LINEAFTER",     (0,0),(-2,-1), 0.25, _MGR),
        ("TOPPADDING",    (0,0),(-1,-1), 8),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
    ]))
    return t


def _decor(cv, doc, rdate, cline):
    W, H = _letter
    cv.saveState()
    # Top green bar
    cv.setFillColor(_DG)
    cv.rect(0, H-0.46*_inch, W, 0.46*_inch, fill=1, stroke=0)
    # Mini N logo badge in header
    import io as _io2, base64 as _b642
    _raw = _b642.b64decode(_LOGO_INTERIOR)
    from reportlab.platypus import Image as _RLI2
    _badge = _RLI2(_io2.BytesIO(_raw), width=0.30*_inch, height=0.30*_inch)
    _badge.drawOn(cv, 0.18*_inch, H-0.43*_inch)
    # Header text
    cv.setFillColor(_WH)
    cv.setFont("Helvetica-Bold", 8.5)
    cv.drawString(0.53*_inch, H-0.285*_inch, "NANOWEAVE")
    cv.setFont("Helvetica", 7.5)
    cv.setFillColor(_colors.HexColor("#A7F3D0"))
    cv.drawString(1.44*_inch, H-0.285*_inch,
                  "Techno-Economic Assessment  |  Confidential")
    cv.setFillColor(_WH)
    cv.drawRightString(W-0.45*_inch, H-0.285*_inch, rdate)
    # Accent line
    cv.setStrokeColor(_GL)
    cv.setLineWidth(0.5)
    cv.line(0, H-0.46*_inch, W, H-0.46*_inch)
    # Footer bar
    cv.setFillColor(_LGR)
    cv.rect(0, 0, W, 0.38*_inch, fill=1, stroke=0)
    cv.setStrokeColor(_MGR); cv.setLineWidth(0.25)
    cv.line(0, 0.38*_inch, W, 0.38*_inch)
    cv.setFillColor(_MGR)
    cv.setFont("Helvetica", 6.8)
    cv.drawString(0.5*_inch, 0.13*_inch, cline[:115])
    cv.setFillColor(_SL)
    cv.setFont("Helvetica-Bold", 7.5)
    cv.drawRightString(W-0.5*_inch, 0.13*_inch, f"Page {doc.page}")
    cv.restoreState()


def generate_pdf_report(
    biomass_total, biomass_key, power_mode, elec_price,
    cell_price, fert_price, depr_years, logistics_cost,
    n_franchisees, modules_per_fr, lifespan, rampup_months,
    hq_opex, sell_price, dev_capex,
):
    """Build full 7-section TEA PDF. Returns bytes."""
    elec_price     = elec_price     or 0.08
    cell_price     = cell_price     or 900
    fert_price     = fert_price     or 350
    depr_years     = depr_years     or 15
    logistics_cost = logistics_cost or 6.0
    n_franchisees  = n_franchisees  or 5
    modules_per_fr = modules_per_fr or 8
    lifespan       = lifespan       or 15
    rampup_months  = rampup_months  or 12
    hq_opex        = hq_opex        or 800_000
    sell_price     = sell_price     or 900
    dev_capex      = dev_capex      or 2_000_000

    r  = run_calculations(biomass_total, biomass_key, power_mode, elec_price,
                          cell_price, fert_price, depr_years, logistics_cost)
    r1 = run_calculations(50, biomass_key, power_mode, elec_price,
                          sell_price, fert_price, depr_years, logistics_cost)
    bd = BIOMASS_DATA[biomass_key]
    dm = 1 - bd["moisture"]

    # ── Franchise scenarios (full P&L, correct CAPEX ownership) ──────────
    LICENSE_PCT = 0.05
    # Franchisee leases equipment — CAPEX converted to annual lease payment
    # Lease = CAPEX ÷ lifespan (straight-line, recovered over project lifetime)
    # Total franchisee OPEX = cash OPEX + annual lease
    # Buy price basis = total OPEX per ton (incl. lease)
    # EBITDA = revenue − total OPEX (after lease cost)
    fr_cash_opex_y = (r1["energy_yr"] + r1["naoh_yr"] +
                      r1["labor_yr"] + r1["water_yr"] +
                      r1["logistics_yr"])
    fr_capex_mod   = r1["capex_total"]
    fr_lease_y     = fr_capex_mod / lifespan            # annual lease payment
    fr_total_opex_y = fr_cash_opex_y + fr_lease_y       # total incl. lease
    total_opex_t   = fr_total_opex_y / r1["total_cell_yr"] if r1["total_cell_yr"] > 0 else 0
    fr_sc = {}
    for lbl, mp in [("+10%", 0.10), ("+15%", 0.15), ("+20%", 0.20)]:
        buy_t     = total_opex_t * (1 + mp)            # covers all costs incl. lease
        cell_y    = r1["total_cell_yr"]
        fr_rev    = buy_t * cell_y
        fr_ebitda = fr_rev - fr_total_opex_y            # after lease cost
        fr_marg   = (fr_ebitda / fr_rev * 100) if fr_rev > 0 else 0
        lic       = max(0, fr_ebitda * LICENSE_PCT)
        trade     = (sell_price - buy_t) * cell_y
        dev_inc   = trade + lic
        tot_mods  = n_franchisees * modules_per_fr
        fr_sc[lbl] = dict(
            buy_t=buy_t, fr_rev=fr_rev, fr_ebitda=fr_ebitda,
            fr_marg=fr_marg, lic=lic, trade=trade, dev_inc=dev_inc,
            tot_fr_rev=tot_mods*fr_rev, tot_fr_opex=tot_mods*fr_total_opex_y,
            tot_fr_ebitda=tot_mods*fr_ebitda,
            tot_dev_trade=tot_mods*trade, tot_dev_lic=tot_mods*lic,
            tot_dev_inc=tot_mods*dev_inc,
        )

    # ── Franchise ramp-up + CF ────────────────────────────────────────────
    fr_on  = [(i*rampup_months/12) for i in range(1, n_franchisees+1)]
    years  = list(range(1, lifespan+1))

    def amods(yr):
        tot = 0.0
        for oy in fr_on:
            if yr > oy:          tot += modules_per_fr
            elif yr > oy - 1:    tot += modules_per_fr * max(0, yr - oy)
        return tot

    cf_by_sc = {}
    for lbl, s in fr_sc.items():
        ann, cum, run = [], [], -dev_capex
        for yr in years:
            net = amods(yr)*s["dev_inc"] - hq_opex - dev_capex/depr_years
            ann.append(net); run += net; cum.append(run)
        cf_by_sc[lbl] = {"ann": ann, "cum": cum}

    bep = next((years[i] for i,c in enumerate(cf_by_sc["+15%"]["cum"]) if c>=0), None)

    # ── Document setup ────────────────────────────────────────────────────
    buf  = _io.BytesIO()
    W, H = _letter
    M    = 0.5*_inch
    CW   = W - 2*M
    rdate = _dt.now().strftime("%d %b %Y")
    cline = (f"Feedstock: {bd['label']}  |  Power: {power_mode.upper()}  |  "
             f"${cell_price}/t  |  {r['n_modules']} modules  |  "
             f"Logistics: ${logistics_cost}/t  |  Boiler+Turbine $500K each/mod")
    st = _ss()
    doc = _Doc(buf, pagesize=_letter,
               leftMargin=M, rightMargin=M,
               topMargin=0.62*_inch, bottomMargin=0.5*_inch)
    s = []

    # ══════════════════════════════════════════════════════════════════════
    # COVER
    # ══════════════════════════════════════════════════════════════════════
    cover_rows = [
        [_logo(1.15, 0.71)],
        [_Sp(1, 6)],
        [_P("NANOWEAVE", st["ct"])],
        [_P("Upcycling Nature", _PS("CU", fontSize=11,
            textColor=_GL, fontName="Helvetica", alignment=_TAC, leading=14))],
        [_Sp(1, 12)],
        [_P("Techno-Economic Assessment Report", st["cs"])],
        [_P("Plasma Biorefinery  ·  Modular 50 TPD System  ·  Boiler + Steam Turbine CHP",
            _PS("CI", fontSize=10, textColor=_colors.HexColor("#6EE7B7"),
            fontName="Helvetica-Oblique", alignment=_TAC, leading=13))],
        [_Sp(1, 18)],
        [_P(f"Generated: {rdate}  ·  Confidential", st["cm"])],
    ]
    ctbl = _T([[r[0]] for r in cover_rows], colWidths=[CW])
    ctbl.setStyle(_TS([
        ("BACKGROUND",(0,0),(-1,-1),_DG),
        ("TOPPADDING",(0,0),(-1,-1),14), ("BOTTOMPADDING",(0,0),(-1,-1),14),
        ("LEFTPADDING",(0,0),(-1,-1),20), ("RIGHTPADDING",(0,0),(-1,-1),20),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    s += [_Sp(1, 0.9*_inch), ctbl, _Sp(1, 0.22*_inch)]
    s.append(_dt_row([
        ["Parameter", "Value"],
        ["Feedstock",          bd["label"]],
        ["Biomass input",      f"{biomass_total} TPD wet"],
        ["Power mode",         power_mode.upper()],
        ["CHP system",         "Boiler $500K + Steam Turbine $500K per module"],
        ["Modules",            str(r["n_modules"])],
        ["Cellulose output",   f"{r['total_cell_day']:.1f} t/day · {r['total_cell_yr']:,.0f} t/yr"],
        ["Cellulose price",    f"${cell_price}/ton"],
        ["Logistics cost",     f"${logistics_cost}/ton wet biomass"],
        ["Franchisees",        f"{n_franchisees} franchisees × {modules_per_fr} modules"],
        ["Project lifespan",   f"{lifespan} years"],
        ["Dev. break-even",    f"Year {bep}" if bep else "Beyond lifespan"],
    ], [CW*0.40, CW*0.60], hbg=_MG))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S1 — EXECUTIVE SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("1.  Executive Summary", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_kpi_block([
        ("Modules",        str(r["n_modules"])),
        ("Cellulose/yr",   f"{r['total_cell_yr']/1000:.1f}K t"),
        ("Total CAPEX",    fmt_usd(r["capex_total"])),
        ("OPEX/yr",        fmt_usd(r["total_opex"])),
        ("EBITDA/yr",      fmt_usd(r["margin"])),
        ("Dev Break-even", f"Yr {bep}" if bep else "N/A"),
    ], CW, st))
    s.append(_Sp(1, 10))
    s.append(_P(
        f"Processing <b>{biomass_total} TPD</b> of {bd['label']} in "
        f"<b>{r['n_modules']}-module</b> {power_mode.upper()} configuration. "
        f"CHP system: <b>Boiler + Steam Turbine at $500K each per module</b> "
        f"(${1_000_000*r['n_modules']:,} total CHP CAPEX). "
        f"Logistics at <b>${logistics_cost}/t</b> adds "
        f"<b>{fmt_usd(r['logistics_yr'])}/yr</b> to OPEX. "
        f"At ${cell_price}/t cellulose: <b>{fmt_usd(r['margin'])}</b> EBITDA "
        f"({r['margin_pct']:.1f}% margin). "
        f"Developer franchise break-even: "
        f"<b>Year {bep if bep else 'beyond lifespan'}</b> (+15% scenario).",
        st["body"],
    ))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S2 — MASS & ENERGY BALANCE
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("2.  Mass & Energy Balance", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))

    s.append(_P("2.1  Feedstock Composition", st["h2"]))
    s.append(_dt_row([
        ["Parameter", "Value", "Basis"],
        ["Moisture (as-received)", f"{bd['moisture']*100:.0f}%",      "Biomass Composition Sheet"],
        ["Dry matter fraction",    f"{dm*100:.0f}%",                  "= 1 − moisture"],
        ["Cellulose (% DM)",       f"{bd['cellulose_dm']*100:.0f}%",  "Biomass Composition Sheet"],
        ["Lignin (% DM)",          f"{bd['lignin_dm']*100:.0f}%",     "Literature"],
        ["Ash (% DM)",             f"{bd['ash_dm']*100:.0f}%",        "Literature"],
        ["N-fixation rate",        f"{bd['n_fixation']*1000:.1f} g/kg DM", "Plasma model"],
        ["LHV (dry basis)",        f"{bd['lhv_gj_t_dry']} GJ/t",     "Literature"],
    ], [CW*0.38, CW*0.24, CW*0.38]))
    s.append(_Sp(1, 10))

    s.append(_P("2.2  Mass Balance — per 50 TPD Module", st["h2"]))
    cell_pm     = 50*dm*bd["cellulose_dm"]*PROC_EFFICIENCY
    n_pm        = 50*dm*bd["n_fixation"]
    residual_dm = (50*dm) - cell_pm
    fert_total  = residual_dm + n_pm
    s.append(_dt_row([
        ["Stream", "t/day", "t/yr (330 d)", "Notes"],
        ["Wet biomass in",      "50.0",              f"{50*330:,.0f}",
                                "Module design basis"],
        ["Dry matter",          f"{50*dm:.1f}",      f"{50*dm*330:,.0f}",
                                f"{dm*100:.0f}% of wet input"],
        ["Water released",      f"{50*bd['moisture']:.1f}",
                                f"{50*bd['moisture']*330:,.0f}",
                                "Released in processing"],
        ["── OUTPUTS ──",       "──",                "──",                "──"],
        ["Cellulose (bleached)",f"{cell_pm:.2f}",    f"{cell_pm*330:,.0f}",
                                f"DM × cel% × {PROC_EFFICIENCY*100:.0f}% eff."],
        ["Residual biomass",    f"{residual_dm:.2f}",f"{residual_dm*330:,.0f}",
                                "Lignin + ash → humic/fulvic carrier for fertilizer"],
        ["N-fixation stream",   f"{n_pm:.3f}",       f"{n_pm*330:,.0f}",
                                "Plasma NO₃⁻ + NH₄⁺ liquid — combined with residual"],
        ["Organic fertilizer",  f"{fert_total:.2f}", f"{fert_total*330:,.0f}",
                                "Residual biomass + N stream · humic/fulvic + minerals + N"],
    ], [CW*0.24, CW*0.13, CW*0.17, CW*0.46], bold_last=1))
    s.append(_Sp(1, 4))
    s.append(_P(
        f"<b>Fertilizer composition:</b> The organic fertilizer combines the lignin-rich "
        f"residual biomass (humic and fulvic acid precursors, mineral fraction) with the "
        f"nitrogen-rich plasma liquid stream (nitrate + ammonium). This produces a "
        f"complete organic soil amendment — not just a nitrogen source — with {n_pm:.3f} t/day "
        f"of N-equivalent and {residual_dm:.2f} t/day of organic matter carrier per module.",
        st["cap"],
    ))
    s.append(_Sp(1, 10))

    s.append(_P("2.3  Energy Balance — per 50 TPD Module", st["h2"]))
    s.append(_dt_row([
        ["Process Stage", "kWh/day", "kWh/t pulp", "% total", "Source"],
        ["Refining",          "1,081", f"{1081/cell_pm:.0f}", "21%", "Industry avg. [Output Sheet]"],
        ["Plasma cavitation", "3,388", f"{3388/cell_pm:.0f}", "67%", "Nanoweave lab data"],
        ["Mech. Separation",    "600", f"{600/cell_pm:.0f}",  "12%", "Industry avg. (steam heat)"],
        ["TOTAL", f"{TOTAL_ENERGY_MODULE:,}", f"{TOTAL_ENERGY_MODULE/cell_pm:.0f}", "100%", ""],
    ], [CW*0.22, CW*0.13, CW*0.13, CW*0.10, CW*0.42], bold_last=1))
    s.append(_Sp(1, 4))
    if power_mode != "grid":
        s.append(_P(
            f"<b>CHP:</b> Boiler combusts residual lignin/biomass fraction · "
            f"Steam drives turbine at 20% electrical eff. · "
            f"{r['biomass_chp']:.1f} t/day surplus biomass burned · "
            f"CHP trigger: gate cost &lt; $26/t.",
            st["body"],
        ))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S3 — CAPEX
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("3.  Capital Expenditure (CAPEX)", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_kpi_block([
        ("Process CAPEX",  fmt_usd(r["capex_process"])),
        ("CHP CAPEX",      fmt_usd(r["capex_chp"]) if r["capex_chp"]>0 else "—"),
        ("TOTAL CAPEX",    fmt_usd(r["capex_total"])),
        ("Infra saving",   f"-{fmt_usd(r['capex_site_save'])}" if r["capex_site_save"]>0 else "—"),
        ("$/t·yr",         f"${r['capex_intensity']:,.0f}"),
    ], CW, st))
    s.append(_Sp(1, 6))
    s.append(_P(
        "CHP system: <b>1 Boiler ($500K) + 1 Steam Turbine ($500K) per module = $1M/module</b>. "
        "Steam from boiler drives turbine (20% elec. eff.) and supplies flash dryer heat (50% heat eff.). "
        "Site-fixed items (electrical system, tanks, conveyors, screens) are shared — "
        "generating structural CAPEX savings vs. naive linear scaling.",
        st["body"],
    ))
    s.append(_Sp(1, 6))

    df_c = r["df_capex"]
    cap_rows = [["Category", "Equipment", "Unit ($)", "Qty", "Total ($)", "Saving ($)"]]
    for _, row in df_c.iterrows():
        qs = f"×{int(row['Qty'])} (shared)" if row["Scale"]=="site" else f"×{int(row['Qty'])}"
        ss = f"-{fmt_usd(row['Saving'])}" if row["Saving"]>0 else "—"
        cap_rows.append([row["Category"], row["Equipment"],
                         f"${row['Unit ($)']:,}", qs,
                         fmt_usd(row["Total"]), ss])
    cap_rows.append(["", "TOTAL (Process)", "", "", fmt_usd(r["capex_process"]), ""])
    if r["capex_chp"] > 0:
        cap_rows.append(["", "CHP (Boiler+Turbine)", "", "", fmt_usd(r["capex_chp"]), ""])
    cap_rows.append(["", "GRAND TOTAL", "", "", fmt_usd(r["capex_total"]), ""])
    tbl_c = _dt_row(cap_rows,
                    [CW*0.13, CW*0.27, CW*0.12, CW*0.13, CW*0.18, CW*0.17],
                    bold_last=1 if r["capex_chp"]==0 else 2)
    s.append(tbl_c)
    s.append(_Sp(1, 4))
    s.append(_P(
        f"Kraft mill benchmark: $1,200–$2,000/t·yr.  "
        f"Nanoweave: ${r['capex_intensity']:,.0f}/t·yr.",
        st["cap"],
    ))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S4 — OPEX
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("4.  Operating Expenditure (OPEX)", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_kpi_block([
        ("Energy/yr",       fmt_usd(r["energy_yr"])),
        ("NaOH/yr",         fmt_usd(r["naoh_yr"])),
        ("Labor/yr",        fmt_usd(r["labor_yr"])),
        ("Water/yr",        fmt_usd(r["water_yr"])),
        ("Logistics/yr",    fmt_usd(r["logistics_yr"])),
        ("Depreciation/yr", fmt_usd(r["depr_yr"])),
        ("TOTAL/yr",        fmt_usd(r["total_opex"])),
        ("$/t cellulose",   f"${r['opex_per_t']:,.0f}"),
    ], CW, st))
    s.append(_Sp(1, 8))
    op_d = [["Cost Item", "$/year", "$/t cellulose", "Basis"]]
    for item, val, basis in [
        ("Energy",       r["energy_yr"],    "Grid kWh × $0.08 / $0 if CHP"),
        ("NaOH (alkali)",r["naoh_yr"],      f"{NAOH_T_PER_MOD:.3f} t/day/mod × ${NAOH_PRICE}/t"),
        ("Labor",        r["labor_yr"],     f"${LABOR_PER_MODULE:,}/mod/yr  8 operators"),
        ("Water treat.", r["water_yr"],     f"${WATER_PER_MOD}/day/mod  99% recovery"),
        ("Logistics",    r["logistics_yr"], f"${logistics_cost}/t × {r['biomass_process']:.0f} t/day × {OP_DAYS} days"),
        ("Depreciation", r["depr_yr"],      f"Process CAPEX / {depr_years} yrs"),
    ]:
        pt = f"${val/r['total_cell_yr']:,.0f}" if r["total_cell_yr"]>0 else "—"
        op_d.append([item, fmt_usd(val), pt, basis])
    op_d.append(["TOTAL OPEX", fmt_usd(r["total_opex"]),
                 f"${r['opex_per_t']:,.0f}", ""])
    s.append(_dt_row(op_d, [CW*0.18, CW*0.16, CW*0.14, CW*0.52], bold_last=1))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S5 — REVENUE & MARGIN
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("5.  Revenue & Project Margin", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_kpi_block([
        ("Cellulose rev/yr",  fmt_usd(r["rev_cell_mid"])),
        ("Fertilizer rev/yr", fmt_usd(r["rev_fert"])),
        ("Total revenue/yr",  fmt_usd(r["rev_total"])),
        ("EBITDA/yr",         fmt_usd(r["margin"])),
        ("Margin %",          f"{r['margin_pct']:.1f}%"),
        ("Payback",           f"{r['payback']:.1f} yrs" if r["payback"] else "N/A"),
    ], CW, st))
    s.append(_Sp(1, 10))
    s.append(_P("5.1  Cellulose Price Sensitivity", st["h2"]))
    pps = [600, 700, 800, 900, 1000, 1200, 1400, 1600]
    sens = [["Price ($/t)", "Revenue/yr", "EBITDA", "Margin %", "Payback"]]
    for pp in pps:
        rv = r["total_cell_yr"]*pp + r["rev_fert"]
        mr = rv - r["total_opex"]
        pc = (mr/rv*100) if rv>0 else 0
        pb = f"{r['capex_process']/mr:.1f}" if mr>0 else "N/A"
        tag = " ◀" if pp == cell_price else ""
        sens.append([f"${pp}{tag}", fmt_usd(rv), fmt_usd(mr), f"{pc:.1f}%", pb])
    ts = _dt_row(sens, [CW*0.18, CW*0.22, CW*0.20, CW*0.18, CW*0.22])
    if cell_price in pps:
        ci = pps.index(cell_price)+1
        ts.setStyle(_TS([
            ("BACKGROUND", (0,ci),(-1,ci), _AL),
            ("FONTNAME",   (0,ci),(-1,ci), "Helvetica-Bold"),
        ]))
    s.append(ts)
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S6 — FRANCHISE MODEL
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("6.  Franchise Model — Full P&L & Developer Cash Flow", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_P(
        f"Nanoweave licenses to <b>{n_franchisees} franchisees</b> "
        f"({n_franchisees*modules_per_fr} total modules). "
        f"<b>Franchisee CAPEX is converted to an annual lease payment</b>: "
        f"{fmt_usd(fr_capex_mod)}/module ÷ {lifespan} years = "
        f"<b>{fmt_usd(fr_lease_y)}/module/yr</b> — treated as OPEX. "
        f"Buy price covers all costs: cash OPEX + lease. "
        f"Developer buys at <b>total OPEX + margin</b>, resells at "
        f"<b>${sell_price}/t</b>. "
        f"<b>License: 5% of franchisee EBITDA</b> (after lease) per module per year. "
        f"Ramp-up: {rampup_months} months per franchisee. "
        f"Dev. HQ OPEX: {fmt_usd(hq_opex)}/yr · Dev. own CAPEX: {fmt_usd(dev_capex)}.",
        st["body"],
    ))
    s.append(_Sp(1, 10))

    # ── 6.1 Full P&L table — all 3 scenarios ─────────────────────────────
    s.append(_P("6.1  Scenario P&L — per Module per Year", st["h2"]))
    fr_pldata = [
        ["Scenario", "Buy $/t",
         "Fr. Revenue", "Fr. OPEX", "Fr. EBITDA", "Fr. Margin",
         "Dev Trade", "License (5%)", "Dev Net"]
    ]
    for lbl, sc in fr_sc.items():
        fr_pldata.append([
            lbl,
            f"${sc['buy_t']:,.0f}",
            fmt_usd(sc["fr_rev"]),
            fmt_usd(r1["total_opex"]),
            fmt_usd(sc["fr_ebitda"]),
            f"{sc['fr_marg']:.1f}%",
            fmt_usd(sc["trade"]),
            fmt_usd(sc["lic"]),
            fmt_usd(sc["dev_inc"]),
        ])
    s.append(_dt_row(fr_pldata,
                     [CW*0.08, CW*0.09, CW*0.12, CW*0.12, CW*0.12,
                      CW*0.10, CW*0.12, CW*0.12, CW*0.13]))
    s.append(_Sp(1, 6))
    s.append(_P(
        f"Buy price basis: total OPEX/t = ${total_opex_t:,.0f}/t "
        f"(cash OPEX ${fr_cash_opex_y/r1['total_cell_yr']:,.0f}/t "
        f"+ lease ${fr_lease_y/r1['total_cell_yr']:,.0f}/t). "
        f"Lease = {fmt_usd(fr_capex_mod)} CAPEX ÷ {lifespan} yrs = {fmt_usd(fr_lease_y)}/yr/module. "
        f"Market sell: ${sell_price}/t.",
        st["cap"],
    ))
    s.append(_Sp(1, 10))

    # ── 6.2 Portfolio totals at maturity ─────────────────────────────────
    s.append(_P("6.2  Portfolio Totals at Maturity (all franchisees active)", st["h2"]))
    tot_mods = n_franchisees * modules_per_fr
    port_d = [["Scenario", "Fr. Total Rev.", "Fr. Total OPEX", "Fr. Total EBITDA",
               "Dev Total Trade", "Dev Total License", "Dev Gross Income"]]
    for lbl, sc in fr_sc.items():
        port_d.append([
            lbl,
            fmt_usd(sc["tot_fr_rev"]),
            fmt_usd(sc["tot_fr_opex"]),
            fmt_usd(sc["tot_fr_ebitda"]),
            fmt_usd(sc["tot_dev_trade"]),
            fmt_usd(sc["tot_dev_lic"]),
            fmt_usd(sc["tot_dev_inc"]),
        ])
    s.append(_dt_row(port_d,
                     [CW*0.10, CW*0.15, CW*0.15, CW*0.15, CW*0.15, CW*0.15, CW*0.15]))
    s.append(_Sp(1, 10))

    # ── 6.3 Developer annual cash flow table ─────────────────────────────
    s.append(_P("6.3  Developer Annual Cash Flow — All 3 Scenarios", st["h2"]))
    cf_hdr = ["Year", "Mods"] + \
             [f"+10% Ann.", f"+10% Cum."] + \
             [f"+15% Ann.", f"+15% Cum."] + \
             [f"+20% Ann.", f"+20% Cum."]
    cf_d = [cf_hdr]
    for i, yr in enumerate(years):
        mods = amods(yr)
        row = [str(yr), f"{mods:.0f}"]
        for lbl in ["+10%", "+15%", "+20%"]:
            row += [fmt_usd(cf_by_sc[lbl]["ann"][i]),
                    fmt_usd(cf_by_sc[lbl]["cum"][i])]
        cf_d.append(row)

    tcf = _dt_row(cf_d,
                  [CW*0.06, CW*0.07,
                   CW*0.11, CW*0.11,
                   CW*0.11, CW*0.11,
                   CW*0.11, CW*0.11,
                   CW*0.21])
    # Colour coding: BEP row green, negative cumulative red text
    for i, yr in enumerate(years):
        ri = i+1
        if cf_by_sc["+15%"]["cum"][i] >= 0 and \
           (i==0 or cf_by_sc["+15%"]["cum"][i-1] < 0):
            tcf.setStyle(_TS([
                ("BACKGROUND", (0,ri),(-1,ri), _LG),
                ("FONTNAME",   (0,ri),(-1,ri), "Helvetica-Bold"),
            ]))
        # Mark negative annual CF in red for mid scenario columns
        if cf_by_sc["+15%"]["ann"][i] < 0:
            tcf.setStyle(_TS([("TEXTCOLOR", (4,ri),(5,ri), _RD)]))
    s.append(tcf)
    if bep:
        s.append(_Sp(1, 4))
        s.append(_P(
            f"Developer cumulative break-even: <b>Year {bep}</b> (+15% scenario). "
            f"Green = break-even. Dev. initial CAPEX: {fmt_usd(dev_capex)}.",
            st["cap"],
        ))
    s.append(_PB())

    # ══════════════════════════════════════════════════════════════════════
    # S7 — ASSUMPTIONS
    # ══════════════════════════════════════════════════════════════════════
    s.append(_P("7.  Key Assumptions & Data Sources", st["h1"]))
    s.append(_HR(width=CW, thickness=1.5, color=_GL, spaceAfter=8))
    s.append(_dt_row([
        ["Parameter", "Value", "Source"],
        ["Operating days/yr",     f"{OP_DAYS} days",          "35 days scheduled maintenance"],
        ["Process efficiency",     f"{PROC_EFFICIENCY*100:.0f}%", "Nanoweave Input Sheet"],
        ["NaOH ratio",            "10:1 biomass:NaOH",        "Lab experiment"],
        ["NaOH price",            f"${NAOH_PRICE}/ton",        "Market reference 2025"],
        ["Labor per module/yr",   f"${LABOR_PER_MODULE:,}",   "Output Sheet"],
        ["Water cost",            f"${WATER_PER_MOD}/day/mod", "Output Sheet  99% recovery"],
        ["Biomass logistics",     f"${logistics_cost}/t wet",  "User input — site-specific"],
        ["CHP: Boiler",           "$500,000 per module",       "Nanoweave CHP model (updated)"],
        ["CHP: Steam Turbine",    "$500,000 per module",       "Nanoweave CHP model (updated)"],
        ["CHP elec. efficiency",  "20%",                       "CHP One-Pager"],
        ["CHP heat efficiency",   "50%",                       "CHP One-Pager"],
        ["CHP gate trigger",      "$26/t biomass",             "Nanoweave operational model"],
        ["License fee",           "5% of franchisee EBITDA",  "Nanoweave franchise model"],
        ["CAPEX scaling",         "Fixed site + linear mods",  "Key Figures TEA"],
        ["Kraft CAPEX benchmark", "$1,200–$2,000/t·yr",       "Industry standard"],
        ["Fiber length (output)", "7–15 mm",                   "Lab + P&G independent testing"],
    ], [CW*0.30, CW*0.25, CW*0.45]))
    s.append(_Sp(1, 14))

    # Confidentiality block
    conf_t = _T([
        [_P("NANOWEAVE  ·  UPCYCLING NATURE  ·  CONFIDENTIAL",
            _PS("CF", fontSize=8.5, textColor=_GL, fontName="Helvetica-Bold",
                alignment=_TAC, leading=11))],
        [_P("This report contains proprietary information belonging to Nanoweave Inc. "
            "Intended solely for authorised recipients. Not for distribution. "
            "Provisional US patent filed.",
            _PS("CC", fontSize=7.5, textColor=_WH, fontName="Helvetica",
                alignment=_TAC, leading=11))],
    ], colWidths=[CW])
    conf_t.setStyle(_TS([
        ("BACKGROUND",    (0,0),(-1,-1), _DG),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(-1,-1), 16),
        ("RIGHTPADDING",  (0,0),(-1,-1), 16),
    ]))
    s.append(conf_t)

    doc.build(
        s,
        onFirstPage=lambda cv, d: _decor(cv, d, rdate, cline),
        onLaterPages=lambda cv, d: _decor(cv, d, rdate, cline),
    )
    return buf.getvalue()


@app.callback(
    Output("pdf-download", "data"),
    Output("pdf-status",   "children"),
    Input("btn-pdf",       "n_clicks"),
    State("biomass-slider",  "value"),
    State("biomass-type",    "value"),
    State("power-mode",      "value"),
    State("elec-price",      "value"),
    State("cell-price",      "value"),
    State("fert-price",      "value"),
    State("depr-years",      "value"),
    State("logistics-cost",  "value"),
    State("fr-franchisees",  "value"),
    State("fr-modules",      "value"),
    State("fr-lifespan",     "value"),
    State("fr-rampup",       "value"),
    State("fr-hq-opex",      "value"),
    State("fr-sell-price",   "value"),
    State("fr-dev-capex",    "value"),
    prevent_initial_call=True,
)
def download_pdf(
    n_clicks,
    biomass_total, biomass_key, power_mode, elec_price,
    cell_price, fert_price, depr_years, logistics_cost,
    n_franchisees, modules_per_fr, lifespan, rampup_months,
    hq_opex, sell_price, dev_capex,
):
    if not n_clicks:
        return None, ""
    try:
        pdf_bytes = generate_pdf_report(
            biomass_total, biomass_key, power_mode, elec_price,
            cell_price, fert_price, depr_years, logistics_cost,
            n_franchisees, modules_per_fr, lifespan, rampup_months,
            hq_opex, sell_price, dev_capex,
        )
        fname = (f"Nanoweave_TEA_{biomass_key}_{power_mode}_"
                 f"{_dt.now().strftime('%Y%m%d_%H%M')}.pdf")
        return (
            dcc.send_bytes(pdf_bytes, fname),
            f"✅  Report ready — {len(pdf_bytes)//1024} KB  ·  {fname}",
        )
    except Exception as e:
        return None, f"❌  Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  NANOWEAVE TEA DASHBOARD  v1.4")
    print("  Starting... open your browser at:")
    print("  http://127.0.0.1:8050")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8050)), debug=False)
