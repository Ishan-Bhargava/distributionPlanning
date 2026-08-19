"""
OptiFlow - Distributer Planning : Streamlit front-end for the distribution engine.

This is a web-based sibling of the desktop app (optiflow_gui.py) one level up. It
talks to the exact same AWS pipeline: sign in, answer the engine's two Yes/No
questions, press Run, and the reports arrive by email. Nothing here runs the
engine locally and nothing here needs AWS credentials - it only POSTs to the
same HTTPS endpoint the desktop app uses. The endpoint itself is never shown in
this UI - same reasoning as the desktop app's header: this window gets
screen-shared and photographed.

This file (and this folder) is fully self-contained - it does not import or read
anything from outside the "streamlit" folder. Accounts come from
OPTIFLOW_USERS_JSON (a container/CI secret) or, for local/desktop-adjacent use,
from a sibling optiflow_users.json one level up; the password-hashing check is
reimplemented here directly (same PBKDF2-SHA256 scheme as optiflow_auth.py) so
this folder never needs to import that module.

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""

import base64
import hashlib
import hmac as _hmac
import os
import json
import datetime
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

# ------------------------------------------------------------------------- paths
HERE = Path(__file__).resolve().parent
PARENT = HERE.parent                       # DistributerPlanning/ - read-only from here
CONFIG_PATH = PARENT / "optiflow_config.json"
LOCAL_USERS_PATH = PARENT / "optiflow_users.json"
LOGO_PATH = HERE / "assets" / "optiflow_logo.png"

# Local/dev convenience: OPTIFLOW_API_KEY (and anything else below) can live in
# a .env file next to app.py instead of being exported by hand every session.
# load_dotenv() never overrides a variable the environment already has, so a
# real deployment's own env vars (Lightsail, CI, etc.) still win - this is a
# no-op there since no .env file ships in the image.
load_dotenv(HERE / ".env")

# Streamlit Community Cloud has no .env file - secrets are entered in its
# "Secrets" UI instead and only show up in st.secrets, not os.environ. Mirror
# them into os.environ so the rest of this file can keep reading os.environ
# either way. setdefault() means a real env var always wins if both are set.
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

PBKDF2_ITERATIONS = 240_000        # must match optiflow_auth.py exactly


def check_password(user: dict, password: str) -> bool:
    try:
        salt = base64.b64decode(user["salt"])
        expected = base64.b64decode(user["hash"])
    except Exception:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return _hmac.compare_digest(got, expected)


def load_users() -> list:
    """Accounts come from OPTIFLOW_USERS_JSON when set (a container has no sibling
    optiflow_users.json to read - that file, and any real password hash, should
    never be baked into an image or committed to a repo). Falls back to the local
    file for the desktop-adjacent, uncontainerized case this app started as."""
    raw = os.environ.get("OPTIFLOW_USERS_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw).get("users", [])
        except json.JSONDecodeError:
            return []
    if LOCAL_USERS_PATH.is_file():
        try:
            return json.loads(LOCAL_USERS_PATH.read_text(encoding="utf-8")).get("users", [])
        except Exception:
            return []
    return []


def find_user(username: str) -> dict | None:
    u = (username or "").strip().lower()
    return next((x for x in load_users() if x.get("username", "").lower() == u), None)


def has_users() -> bool:
    return bool(load_users())


def authenticate(username: str, password: str) -> tuple:
    """(user_dict, None) on success, (None, reason) on failure - same contract as
    optiflow_auth.authenticate, sourced via load_users()."""
    user = find_user(username)
    if not user:
        return None, "Unknown user or wrong password."
    if not check_password(user, password):
        return None, "Unknown user or wrong password."
    if not user.get("email"):
        return None, (f"{user.get('name') or username} has no email address on "
                      f"file, so the report could not be sent. Ask for it to be added.")
    return user, None

DEFAULT_CONFIG = {
    "aws": {"region": "ap-south-1", "bucket": "", "prefix": "ENGINE/",
            "output_root": "outputs"},
    "cloud_call": "api",
    # no baked-in endpoint - it comes from OPTIFLOW_API_URL (.env / real env var)
    # or ../optiflow_config.json, never a literal in this source file
    "api": {"url": "", "key": "", "key_header": "x-api-key", "timeout_s": 30},
    "email": {"to": "", "cc": "", "send": True, "run_by": ""},
    "reports": "all",
}

REPORT_FILES = [
    "1_Final_Allocation.xlsx",
    "2_Detailed_Allocation.xlsx",
    "3_Cost_Benefit.xlsx",
    "4_Truck_Plan.xlsx",
    "5_MH_TN_MonthEnd_Projection.xlsx",
    "6_Plant_Level_Summary.xlsx",
    "7_Data_Gaps.xlsx",
    "8_Plant_Wise_Allocation.xlsx",
]

# CEAT mark colours, lifted from optiflow.ico, used as the app's accent gradient
CEAT_BLUE = "#0053ab"
CEAT_ORANGE = "#f5822d"

THEMES = {
    "Dark": {
        "bg": "#0b1220", "sidebar": "#0e1626", "card": "#151f34", "card2": "#101a2c",
        "border": "#3a4a6b", "text": "#e8eefb", "muted": "#93a4bb",
        "input_bg": "#0a1120", "code_bg": "#0a1120",
    },
    "Light": {
        "bg": "#f4f6fb", "sidebar": "#ffffff", "card": "#ffffff", "card2": "#f7f9fd",
        "border": "#c3cbdc", "text": "#101827", "muted": "#5b6b85",
        "input_bg": "#f7f9fd", "code_bg": "#eef1f8",
    },
}


def stamp() -> str:
    return datetime.datetime.now().strftime("%d-%b-%Y %H:%M:%S")


@st.cache_data(show_spinner=False)
def load_config() -> dict:
    """Read-only: never writes optiflow_config.json, unlike the desktop app."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.is_file():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key, val in saved.items():
                if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(val)
                else:
                    cfg[key] = val
        except Exception:
            pass
    return cfg


@st.cache_data(show_spinner=False)
def logo_b64() -> str:
    if LOGO_PATH.is_file():
        return base64.b64encode(LOGO_PATH.read_bytes()).decode()
    return ""


def api_url(cfg: dict) -> str:
    return (os.environ.get("OPTIFLOW_API_URL", "").strip()
            or (cfg.get("api", {}).get("url") or "").strip())


def api_key(cfg: dict) -> str:
    return (os.environ.get("OPTIFLOW_API_KEY", "").strip()
            or (cfg.get("api", {}).get("key") or "").strip())


def post_request(cfg: dict, payload: dict):
    """POST the planner's choices to the API - same contract as optiflow_gui.py.

    Returns (response_dict, error_string).
    """
    api = cfg.get("api", {})
    url = api_url(cfg)
    if not url:
        return None, ("No API endpoint - set the OPTIFLOW_API_URL environment "
                      "variable, or api.url in optiflow_config.json.")

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = api_key(cfg)
    if key:
        headers[api.get("key_header") or "x-api-key"] = key

    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=float(api.get("timeout_s", 30)))
        if resp.status_code >= 400:
            detail = resp.text[:300]
            if resp.status_code in (401, 403):
                return None, (f"The API refused us ({resp.status_code}). Check the "
                              f"API key - set OPTIFLOW_API_KEY, or api.key in the "
                              f"config. {detail}")
            if resp.status_code == 404:
                return None, f"The API URL was not found (404). Check api.url. {detail}"
            return None, f"The API returned HTTP {resp.status_code}. {detail}"
        raw = resp.text
        try:
            return (json.loads(raw) if raw.strip() else {}), None
        except json.JSONDecodeError:
            return {"raw": raw[:400]}, None
    except requests.exceptions.RequestException as exc:
        return None, (f"Could not reach the API ({exc}). Check the URL, your "
                      f"network, and whether a proxy is needed.")


def addrs(text: str) -> list:
    return [a.strip() for a in text.replace(";", ",").split(",") if a.strip()]


# ------------------------------------------------------------------------- page
st.set_page_config(page_title="Distribution Planning",
                   page_icon="\U0001F69A", layout="wide")

cfg = load_config()

if "theme" not in st.session_state:
    st.session_state.theme = "Dark"
T = THEMES[st.session_state.theme]

# ------------------------------------------------------------------------- theme CSS
st.markdown(f"""
    <style>
    :root {{
        --accent1: {CEAT_BLUE};
        --accent2: {CEAT_ORANGE};
        --bg: {T['bg']}; --sidebar: {T['sidebar']}; --card: {T['card']};
        --card2: {T['card2']}; --border: {T['border']}; --text: {T['text']};
        --muted: {T['muted']}; --input-bg: {T['input_bg']}; --code-bg: {T['code_bg']};
    }}
    .stApp, [data-testid="stAppViewContainer"] {{
        background: var(--bg) !important; color: var(--text);
    }}
    [data-testid="stHeader"] {{ background: transparent !important; }}
    section[data-testid="stSidebar"] {{
        background: var(--sidebar) !important; border-right: 1px solid var(--border);
    }}
    .block-container {{ padding-top: 1.4rem; }}
    h1, h2, h3, h4, h5, p, span, label, .stMarkdown, .stCaption {{ color: var(--text); }}
    [data-testid="stCaptionContainer"] {{ color: var(--muted) !important; }}

    /* header banner with logo */
    .of-header {{
        display: flex; align-items: center; gap: 16px;
        padding: 18px 24px; border-radius: 18px; margin-bottom: 6px;
        background: linear-gradient(120deg, {CEAT_BLUE}22, {CEAT_ORANGE}18);
        border: 1px solid var(--border);
    }}
    .of-logo {{
        width: 54px; height: 54px; border-radius: 14px; flex-shrink: 0;
        box-shadow: 0 4px 14px rgba(0,0,0,0.18);
    }}
    .of-title {{
        font-size: 2.05rem; font-weight: 800; margin: 0; line-height: 1.1;
        background: linear-gradient(90deg, {CEAT_BLUE}, {CEAT_ORANGE});
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        background-clip: text;
    }}
    .of-subtitle {{ color: var(--muted); font-size: 0.92rem; margin-top: 2px; }}
    .of-badge {{
        display: inline-block; padding: 3px 11px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 700; margin-left: 10px; vertical-align: middle;
    }}
    .of-badge-on {{ background: {CEAT_BLUE}; color: #ffffff; border: 1px solid {CEAT_BLUE}; }}
    .of-badge-off {{ background: var(--card2); color: var(--muted); border: 1px solid var(--border); }}

    /* cards */
    .of-card {{
        background: var(--card); border: 1px solid var(--border); border-radius: 16px;
        padding: 18px 20px 14px; margin-bottom: 14px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
    }}
    .of-card h4 {{ margin-top: 0; }}
    .of-log-card {{
        background: var(--code-bg); border: 1px solid var(--border); border-radius: 14px;
        padding: 4px 4px; margin-bottom: 8px;
    }}
    .of-log-card pre {{ background: transparent !important; border: none !important; margin: 0; }}

    /*
      Streamlit's native widgets carry their own (dark-theme) colours baked into
      generated classes with higher CSS specificity than plain element selectors,
      so every widget below is forced explicitly rather than relying on
      inheriting --text / --bg. Selectors use data-testid, which is Streamlit's
      own stable hook (this targets Streamlit 1.6x's DOM).
    */

    /* text inputs / textareas - the visible box is the *RootElement wrapper, not
       the bare <input>; styling only the input left it borderless and invisible
       against the sidebar/card behind it. Streamlit's own component CSS keeps
       reasserting `border-color` on this wrapper (inserted via CSSOM, so it
       doesn't show up in any <style> textContent and wins ties on that one
       property no matter how specific our selector is) - so the border itself
       is turned off here and an inset box-shadow fakes it instead, since that
       property has no competing rule. */
    html body [data-testid="stTextInputRootElement"],
    html body [data-testid="stTextAreaRootElement"] {{
        background: var(--input-bg) !important;
        border: none !important;
        border-radius: 8px !important;
        box-shadow: 0 0 0 1.5px var(--border) inset !important;
    }}
    html body [data-testid="stTextInputRootElement"]:has(input:focus),
    html body [data-testid="stTextAreaRootElement"]:has(textarea:focus) {{
        box-shadow: 0 0 0 2px {CEAT_BLUE} inset !important;
    }}
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {{
        background: transparent !important; color: var(--text) !important;
        border: none !important; -webkit-text-fill-color: var(--text) !important;
    }}
    [data-testid="stTextInputRootElement"]:has(input:disabled),
    [data-testid="stTextAreaRootElement"]:has(textarea:disabled) {{
        background: var(--card2) !important; opacity: 0.75;
    }}
    [data-testid="stTextInput"] input:disabled, [data-testid="stTextArea"] textarea:disabled {{
        color: var(--muted) !important; -webkit-text-fill-color: var(--muted) !important;
    }}
    [data-testid="stWidgetLabel"] p {{ color: var(--text) !important; }}

    /* buttons: secondary (default) and primary */
    [data-testid^="stBaseButton-"] {{
        border-radius: 10px !important;
        transition: transform 0.12s ease, box-shadow 0.12s ease;
    }}
    [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-header"] {{
        background: var(--card2) !important; color: var(--text) !important;
        border: 1px solid var(--border) !important;
    }}
    [data-testid^="stBaseButton-"]:hover {{
        transform: translateY(-1px); box-shadow: 0 6px 16px rgba(0,83,171,0.25);
    }}
    [data-testid="stBaseButton-secondary"]:disabled {{
        background: var(--card2) !important; color: var(--muted) !important;
        border-color: var(--border) !important; opacity: 0.6;
    }}
    [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {{
        background: linear-gradient(120deg, {CEAT_BLUE}, {CEAT_ORANGE}) !important;
        border: none !important; color: #ffffff !important; font-weight: 700 !important;
    }}
    [data-testid="stBaseButton-primary"]:disabled {{
        background: var(--card2) !important; color: var(--muted) !important;
        border: 1px solid var(--border) !important; opacity: 0.7;
    }}

    /* segmented control (our Yes/No pickers + the theme switch) */
    [data-testid="stButtonGroup"] button {{
        border-radius: 8px !important; font-weight: 600 !important;
    }}
    [data-testid="stButtonGroup"] button[aria-checked="false"] {{
        background: var(--card2) !important; color: var(--text) !important;
        border: 1px solid var(--border) !important;
    }}
    [data-testid="stButtonGroup"] button[aria-checked="true"] {{
        background: linear-gradient(120deg, {CEAT_BLUE}, {CEAT_ORANGE}) !important;
        color: #ffffff !important; border: 1px solid transparent !important;
    }}

    /* checkboxes and toggle switches share stCheckbox; the switch has role="switch" */
    [data-testid="stCheckbox"] [data-testid="stMarkdownContainer"] p {{
        color: var(--text) !important;
    }}
    [data-testid="stCheckbox"] label > div:nth-of-type(1) {{
        background: var(--card2) !important; border: 1px solid var(--border) !important;
    }}
    [data-testid="stCheckbox"] input:checked ~ div:nth-of-type(1),
    [data-testid="stCheckbox"] label:has(input:checked) > div:nth-of-type(1) {{
        background: {CEAT_BLUE} !important; border-color: {CEAT_BLUE} !important;
    }}

    /* tabs */
    [data-testid="stTabs"] [data-testid="stTab"] p {{ color: var(--muted) !important; font-weight: 600; }}
    [data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] p {{ color: {CEAT_BLUE} !important; }}
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background-color: {CEAT_BLUE} !important; }}
    [data-testid="stTabs"] [data-baseweb="tab-border"] {{ background-color: var(--border) !important; }}

    /* expanders, containers with a border (e.g. st.container(border=True)) */
    [data-testid="stExpander"] {{
        background: var(--card) !important; border: 1px solid var(--border) !important;
        border-radius: 12px !important;
    }}
    div[style*="border"][data-testid="stVerticalBlockBorderWrapper"] {{
        border-color: var(--border) !important; background: var(--card) !important;
    }}

    /* code / log block */
    [data-testid="stCode"] pre, [data-testid="stCode"] code, pre, code {{
        background: var(--code-bg) !important; color: var(--text) !important;
    }}
    [data-testid="stMetricValue"] {{ color: {CEAT_BLUE} !important; }}
    hr {{ border-color: var(--border) !important; }}
    </style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------- header
logo64 = logo_b64()
logo_html = (f'<img class="of-logo" src="data:image/png;base64,{logo64}">'
            if logo64 else "\U0001F69A")
st.markdown(f"""
    <div class="of-header">
        {logo_html}
        <div>
            <div class="of-title">Distribution Planning</div>
            <div class="of-subtitle">CEAT Primary Replacement Distribution Engine
                &nbsp;·&nbsp; runs in AWS &nbsp;·&nbsp; reports arrive by email</div>
        </div>
    </div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------- sign-in
if "user" not in st.session_state:
    st.session_state.user = None

with st.sidebar:
    st.markdown("### \U0001F464 Sign in")
    if st.session_state.user:
        u = st.session_state.user
        st.success(f"**{u.get('name') or u['username']}**\n\n{u['email']}", icon="✅")
        if st.button("Sign out", use_container_width=True):
            st.session_state.user = None
            st.rerun()
    elif not has_users():
        st.warning("No sign-in accounts exist yet. Running is disabled until "
                  "one is created.", icon="⚠️")
        st.caption("Create one from a terminal in the app folder:\n\n"
                   'python optiflow_auth.py add <user> "<full name>" <email>')
    else:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True,
                                              type="primary")
        if submitted:
            with st.spinner("Checking credentials..."):
                user, err = authenticate(username, password)
            if err:
                st.error(err, icon="\U0001F6ab")
            else:
                st.session_state.user = user
                st.toast(f"Welcome, {user.get('name') or user['username']}!", icon="\U0001F44b")
                st.rerun()

    st.divider()
    st.markdown("### \U0001F3A8 Appearance")
    theme_choice = st.segmented_control(
        "Theme", options=["Dark", "Light"], default=st.session_state.theme,
        label_visibility="collapsed")
    if theme_choice and theme_choice != st.session_state.theme:
        st.session_state.theme = theme_choice
        st.rerun()

# ------------------------------------------------------------------------- form
user = st.session_state.user
col_run, col_mail = st.columns([3, 2], gap="large")

with col_run:
    st.markdown('<div class="of-card">', unsafe_allow_html=True)
    st.markdown("#### ⚙️ Run the engine")

    with_tf = st.segmented_control(
        "1 · Run WITH tube & flap kitting?",
        options=[True, False],
        format_func=lambda v: "✅ Yes — with tube & flap" if v else "✖️ No — tyres only",
        default=True,
        help="Yes = tyres ship 1:1:1 with tube + flap. No = tyres only, kitting skipped.",
    )
    if with_tf is None:
        with_tf = True

    realloc = st.segmented_control(
        "2 · Run WITH truck-fill reallocation between DCs?",
        options=[True, False],
        format_func=lambda v: "✅ Yes — reallocate" if v else "✖️ No — full trucks only",
        default=True,
        help=("Yes = shuffle sub-truck lanes to complete other DCs' trucks. "
              "No = each DC keeps its own allocation, full trucks only, planner "
              "consolidates leftovers."),
    )
    if realloc is None:
        realloc = True

    badge = (f'<span class="of-badge {"of-badge-on" if with_tf else "of-badge-off"}">'
            f'Tube &amp; flap: {"WITH" if with_tf else "TYRES ONLY"}</span>'
            f'<span class="of-badge {"of-badge-on" if realloc else "of-badge-off"}">'
            f'Reallocation: {"ON" if realloc else "OFF"}</span>')
    st.markdown(badge, unsafe_allow_html=True)

    st.markdown("")
    if not user:
        st.error("Sign in from the sidebar to run the engine.", icon="\U0001F512")
    run_clicked = st.button("▶️  Run engine", type="primary",
                            use_container_width=True, disabled=not user)
    st.markdown('</div>', unsafe_allow_html=True)

with col_mail:
    st.markdown('<div class="of-card">', unsafe_allow_html=True)
    tab_mail, tab_reports = st.tabs(["\U0001F4E7 Email", "\U0001F4C4 Reports"])

    with tab_mail:
        send_mail = st.toggle("Email the reports when the run finishes", value=True)
        st.caption("The Lambda sends this with SES after the run. Reports also stay "
                  "in S3, and the mail carries download links for anything too "
                  "large to attach.")
        to_default = user["email"] if user else ""
        cc_default = (user.get("cc") if user else "") or ""
        runby_default = (user.get("name") or user.get("username")) if user else ""
        to_var = st.text_input("To", value=to_default, disabled=not user)
        cc_var = st.text_input("Cc", value=cc_default, disabled=not user)
        runby_var = st.text_input("Run by", value=runby_default, disabled=not user)
        st.caption("separate several addresses with commas")

    with tab_reports:
        st.caption("Attach these reports")
        if "report_picks" not in st.session_state:
            st.session_state.report_picks = {name: True for name in REPORT_FILES}
        pick_cols = st.columns(2)
        for i, name in enumerate(REPORT_FILES):
            label = name.split("_", 1)[1].replace(".xlsx", "").replace("_", " ")
            with pick_cols[i % 2]:
                st.session_state.report_picks[name] = st.checkbox(
                    label, value=st.session_state.report_picks[name], key=f"rep_{name}")
        n_picked = sum(st.session_state.report_picks.values())
        st.progress(n_picked / len(REPORT_FILES),
                   text=f"{n_picked} of {len(REPORT_FILES)} reports selected")
        c1, c2 = st.columns(2)
        if c1.button("Select all", use_container_width=True):
            for name in REPORT_FILES:
                st.session_state.report_picks[name] = True
            st.rerun()
        if c2.button("Clear", use_container_width=True):
            for name in REPORT_FILES:
                st.session_state.report_picks[name] = False
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("#### \U0001F4DC Log")
log_box = st.container()

if "log_lines" not in st.session_state:
    st.session_state.log_lines = ["Ready. Answer the two questions, then press Run engine."]


def log(text: str):
    st.session_state.log_lines.append(text)


if run_clicked and not user:
    # belt-and-suspenders: the button is disabled without a signed-in user, but
    # never trust client-side disabling alone to gate a real submission.
    st.error("Sign in before running the engine.")
elif run_clicked:
    chosen = [n for n, v in st.session_state.report_picks.items() if v]
    reports = "all" if len(chosen) == len(REPORT_FILES) else chosen
    to_list = addrs(to_var)

    if send_mail and not to_list:
        st.warning("'Email the reports' is ticked but the To field is empty. "
                  "Sign in or fill in an address on the Email tab, then run again.",
                  icon="✉️")
    else:
        payload = {
            "with_tube_flap": with_tf,
            "reallocate": realloc,
            "send_email": bool(send_mail) and bool(to_list),
            "email_to": to_list,
            "email_cc": addrs(cc_var),
            "reports": reports,
            "run_by": runby_var.strip(),
        }

        st.session_state.log_lines = []
        log(f"[{stamp()}] submitting run request")
        log(f"[{stamp()}] tube & flap : {'WITH' if with_tf else 'TYRES ONLY'}")
        log(f"[{stamp()}] realloc     : {'ON' if realloc else 'OFF'}")
        if payload["send_email"]:
            log(f"[{stamp()}] will email  : {', '.join(to_list)}")
        log("-" * 74)

        with st.status("Submitting to AWS...", expanded=True) as status:
            resp, error = post_request(cfg, payload)
            if error:
                status.update(label="Not submitted", state="error")
            else:
                status.update(label="Submitted — running in AWS", state="complete")

        if error:
            log(f"[{stamp()}] not submitted - {error}")
            st.error("Not submitted — see the log below.", icon="❌")
        else:
            for field in ("request_key", "key", "run_id", "message", "raw"):
                if isinstance(resp, dict) and resp.get(field):
                    log(f"[{stamp()}] {field}: {resp[field]}")
            log(f"[{stamp()}] Submitted. The engine is running in AWS.")
            if payload["send_email"]:
                log(f"[{stamp()}] The reports will arrive by email at "
                   f"{', '.join(to_list)} in a few minutes.")
            else:
                out_root = (cfg["aws"].get("output_root") or "outputs").strip("/")
                log(f"[{stamp()}] No email was requested, so check the "
                   f"{out_root}/ folder in S3 for the results.")
            st.success("Submitted. The engine is running in AWS.", icon="\U0001F680")

with log_box:
    st.markdown('<div class="of-log-card">', unsafe_allow_html=True)
    st.code("\n".join(st.session_state.log_lines), language=None)
    st.markdown('</div>', unsafe_allow_html=True)

st.caption("Each run keeps its own folder in S3 with a run.json. · "
          "Streamlit front-end — same AWS pipeline as the desktop app.")
