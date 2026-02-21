import streamlit as st
import sqlite3
import hashlib
from datetime import datetime, timedelta
import pandas as pd
import base64
import os
import io

from quran_helpers import (
    fetch_surah, fetch_ayah, summarize_text, tts_generate_mp3_bytes,
    fetch_editions, tts_cached_mp3_bytes, fetch_reciter_audio_by_url, fetch_surah_list
)

# --- Page config ---
st.set_page_config(page_title="Al-Furqaan by Waleed -Read & Reflect", page_icon="📿", layout="centered")

# --- Gold theme CSS ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,700;1,400&family=Inter:wght@300;400;600&display=swap');
:root{--gold:#C9A84C;--gold-2:#E8D5A3;--bg:#FFF9F2;--muted:#6b5b45;}
html,body,[class*="css"]{font-family:Inter, sans-serif!important;background:var(--bg)!important;color:var(--muted)!important}
#MainMenu, footer, header{visibility:hidden}
.block-container{padding:1.2rem 1.2rem 2rem!important;max-width:760px}
.stButton>button{background:linear-gradient(90deg,var(--gold),#b0832e)!important;color:#fff!important;border:none!important}
h1{font-family:'Cormorant Garamond', serif; color:var(--muted)}
.card{background:#fff;border-radius:14px;padding:1rem;border:1px solid #f0e6d6}
.badge{display:inline-block;background:linear-gradient(90deg,var(--gold),#b0832e);color:#fff;padding:0.35rem 0.6rem;border-radius:999px;font-weight:600}
.small{font-size:0.85rem;color:#8a7a66}
.gold-hr{height:6px;background:linear-gradient(90deg,var(--gold),#b0832e);border-radius:6px;margin:0.6rem 0}
.hero {
    background: linear-gradient(90deg, rgba(201,168,76,0.08), rgba(201,168,76,0.03));
    border-radius: 14px;
    padding: 28px;
    display: flex;
    gap: 20px;
    align-items: center;
}
.hero h1 { font-family: 'Cormorant Garamond', serif; font-size:34px; margin:0; color:var(--muted); }
.hero p { margin:6px 0 0 0; color:#6b5b45 }
.hero .cta { margin-top:12px }
.hero .meta { color:#8a7a66; font-size:0.95rem }
</style>
""", unsafe_allow_html=True)

# Load fonts and icons (Google Fonts and FontAwesome)
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300;0,400;0,600;0,700;1,300;1,400;1,600;1,700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Scheherazade+New:wght@400;500;600;700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Amiri:ital,wght@0,400;0,700;1,400;1,700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@100;200;300;400;500;600;700;800;900&amp;family=Libre+Bodoni:wght@400;500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://pro.fontawesome.com/releases/v5.10.0/css/all.css" integrity="sha384-AYmEC3Yw5cVb3ZcuHtOA93w35dYTsvhLPVnYs9eStHfGJvOvKxVfELGroGkvsg+p" crossorigin="anonymous" />
""", unsafe_allow_html=True)

# --- DB setup (separate DB file) ---
DB_PATH = "al_furqaan.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
# users: username primary, password_hash, display_name, points, joined
c.execute('''CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, password_hash TEXT, display_name TEXT, points INTEGER DEFAULT 0, joined_date TEXT
)''')
# reading_logs: id, username, surah, ayah, date
c.execute('''CREATE TABLE IF NOT EXISTS reading_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, surah INTEGER, ayah INTEGER, created_date TEXT
)''')
# achievements: username, key, unlocked_date
c.execute('''CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, key TEXT, unlocked_date TEXT
)''')
conn.commit()

# --- DB migrations: add user preference columns if missing ---
existing_cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
for col, defval, coltype in [
    ("default_translation", "'en.sahih'", "TEXT"),
    ("default_ayahs_per_page", "20", "INTEGER"),
    ("reciter_url", "''", "TEXT"),
    ("mushaf_mode", "0", "INTEGER")
]:
    if col not in existing_cols:
        c.execute(f"ALTER TABLE users ADD COLUMN {col} {coltype} DEFAULT {defval}")
        conn.commit()

# --- Helpers ---

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def safe_rerun():
    """Attempt to rerun the Streamlit app. If experimental_rerun is unavailable or raises, stop execution safely."""
    try:
        if hasattr(st, "experimental_rerun") and callable(st.experimental_rerun):
            st.experimental_rerun()
        else:
            st.stop()
    except Exception:
        # If rerun fails for any reason, stop further execution to avoid cascading errors.
        try:
            st.stop()
        except Exception:
            pass


def add_log(username, surah, ayah):
    c.execute("INSERT INTO reading_logs VALUES (NULL,?,?,?,?)", (username, surah, ayah, datetime.today().strftime("%Y-%m-%d")))
    c.execute("UPDATE users SET points=points+? WHERE username=?", (5, username))
    conn.commit()


def compute_streak_for_user(username: str) -> int:
    df = pd.read_sql_query("SELECT created_date FROM reading_logs WHERE username=? ORDER BY created_date DESC", conn, params=(username,))
    if df.empty: return 0
    df['date'] = pd.to_datetime(df['created_date'])
    dates = sorted(df['date'].dt.date.unique(), reverse=True)
    streak = 0
    expected = datetime.today().date()
    for d in dates:
        if d == expected or d == expected - timedelta(days=1):
            streak += 1
            expected = d - timedelta(days=1)
        else:
            break
    return streak


def award_if_eligible(username: str):
    # check 3-day and 7-day streaks
    streak = compute_streak_for_user(username)
    now = datetime.today().strftime("%Y-%m-%d")
    if streak >= 3:
        if not c.execute("SELECT 1 FROM achievements WHERE username=? AND key=?", (username, '3_day_streak')).fetchone():
            c.execute("INSERT INTO achievements VALUES (NULL,?,?,?)", (username, '3_day_streak', now))
            c.execute("UPDATE users SET points=points+? WHERE username=?", (20, username))
    if streak >= 7:
        if not c.execute("SELECT 1 FROM achievements WHERE username=? AND key=?", (username, '7_day_streak')).fetchone():
            c.execute("INSERT INTO achievements VALUES (NULL,?,?,?)", (username, '7_day_streak', now))
            c.execute("UPDATE users SET points=points+? WHERE username=?", (75, username))
    conn.commit()

# --- Session state ---
for k,v in [("logged_in", False), ("user", None), ("surah_cache", {})]:
    if k not in st.session_state: st.session_state[k] = v

# --- Auth UI ---
# Hero / header inspired by the design
st.markdown(f"""
<div class="hero">
    <div style="flex:1">
        <h1>Al-Furqaan by Waleed</h1>
        <div class="meta">Read, recite and reflect — translations, transliteration and recitations.</div>
        <p class="small">Explore the Quran with a calm, gold-accented reader. Sign in to track your reading, build streaks, and earn awards.</p>
        <div class="cta">
            <!-- CTA button rendered by Streamlit below -->
        </div>
    </div>
    <div style="width:240px;text-align:right">
        <div style="font-size:0.95rem;font-weight:700;color:var(--muted)">Available</div>
        <div style="margin-top:8px" class="small">Translations: <strong>Multiple</strong></div>
        <div style="margin-top:6px" class="small">Recitations: <strong>Several</strong></div>
    </div>
</div>
""", unsafe_allow_html=True)

# CTA: Start Reading (keeps behavior simple: shows auth or reader)
if st.button("Start Reading", key="hero_start"):
        # If user not logged in, scroll to auth by rerunning; the auth UI is below
        st.session_state.start_reading = True
        safe_rerun()

st.markdown('<div class="gold-hr"></div>', unsafe_allow_html=True)

if not st.session_state.logged_in:
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Sign In")
        si_user = st.text_input("Username", key="si_user")
        si_pw = st.text_input("Password", type="password", key="si_pw")
        if st.button("Sign In"):
            row = c.execute("SELECT password_hash FROM users WHERE username=?", (si_user,)).fetchone()
            if row and row[0] == hash_pw(si_pw):
                st.session_state.logged_in = True
                st.session_state.user = si_user
                st.success("Signed in")
                safe_rerun()
            else:
                st.error("Invalid credentials")
    with cols[1]:
        st.subheader("Create Account")
        ca_user = st.text_input("Choose username", key="ca_user")
        ca_pw = st.text_input("Choose password", type="password", key="ca_pw")
        ca_name = st.text_input("Display name (optional)", key="ca_name")
        if st.button("Create Account"):
            if not ca_user or not ca_pw:
                st.warning("Enter username and password")
            elif c.execute("SELECT 1 FROM users WHERE username=?", (ca_user,)).fetchone():
                st.error("Username taken")
            else:
                # Insert with new preference columns (they exist thanks to migration above)
                c.execute("INSERT INTO users (username,password_hash,display_name,points,joined_date,default_translation,default_ayahs_per_page,reciter_url,mushaf_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                          (ca_user, hash_pw(ca_pw), ca_name or ca_user, 0, datetime.today().strftime('%Y-%m-%d'), 'en.sahih', 20, '', 0))
                conn.commit()
                st.success("Account created — please sign in")
else:
    # Main app
    user = st.session_state.user
    ur = c.execute("SELECT display_name, points, joined_date, default_translation, default_ayahs_per_page, reciter_url, mushaf_mode FROM users WHERE username=?", (user,)).fetchone()
    display_name, points, joined, user_default_translation, user_default_ayahs, user_reciter_url, user_mushaf_mode = ur
    header_cols = st.columns([3,1])
    with header_cols[0]:
        st.markdown(f"<div style='font-size:1.05rem;font-weight:700'>{display_name}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='small'>@{user} · Joined {joined}</div>", unsafe_allow_html=True)
    with header_cols[1]:
        st.markdown(f"<div style='text-align:right;'><span class='badge'>{points} pts</span></div>", unsafe_allow_html=True)

    st.markdown('<div class="gold-hr"></div>', unsafe_allow_html=True)

    # Create the main tabs, with Surah index first
    tab = st.tabs(["Surahs","Reader","My Progress","Achievements","Settings"])

    # --- Tab 0: Surah index (grid) ---
    with tab[0]:
        st.subheader("Surah Index")
        # Load or fetch surah list (cached in session)
        if 'surah_list' not in st.session_state or not st.session_state.surah_list:
            try:
                st.session_state.surah_list = fetch_surah_list()
            except Exception:
                st.session_state.surah_list = []

        surahs = st.session_state.get('surah_list', [])
        if not surahs:
            st.info("Surah list unavailable. Try again later or check network.")
        else:
            # Grid: 6 columns to resemble the provided design
            cols_per_row = 6
            rows = (len(surahs) + cols_per_row - 1) // cols_per_row
            idx = 0
            for r in range(rows):
                cols_row = st.columns(cols_per_row)
                for col in cols_row:
                    if idx >= len(surahs):
                        col.write("")
                    else:
                        s = surahs[idx]
                        num = s.get('number')
                        arabic = s.get('name')
                        eng = s.get('englishName') or s.get('englishNameTranslation','')
                        verses = s.get('numberOfAyahs', '')
                        # card HTML
                        card_html = f"""
                        <div class='card' style='text-align:center;padding:0.6rem;margin:6px'>
                          <div style='display:flex;align-items:center;justify-content:center'>
                            <div style='width:56px;height:56px;border-radius:999px;background:linear-gradient(90deg,var(--gold),#b0832e);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;margin-bottom:6px'>{num}</div>
                          </div>
                          <div style='font-family: "Scheherazade New", serif; font-size:20px; margin-top:4px'>{arabic}</div>
                          <div style='font-weight:600;color:#3e352b;margin-top:4px'>{eng}</div>
                          <div class='small' style='margin-top:4px;color:#7a6a53'>{verses} Verses</div>
                        </div>
                        """
                        col.markdown(card_html, unsafe_allow_html=True)
                        # button to open this surah
                        if col.button("Open", key=f"open_surah_{num}"):
                            # fetch and set current surah in session then switch to Reader (tab index 1)
                            try:
                                ar = fetch_surah(num, 'ar.alafasy')
                                tr = fetch_surah(num, user_default_translation or 'en.sahih')
                                st.session_state._current_surah_ar = ar.get('data')
                                st.session_state._current_surah_tr = tr.get('data')
                                # attempt transliteration
                                try:
                                    trl = fetch_surah(num, 'en.transliteration')
                                    st.session_state._current_surah_trl = trl.get('data')
                                except Exception:
                                    st.session_state._current_surah_trl = None
                                st.session_state.surah_page = 0
                                st.session_state.ayahs_per_page = user_default_ayahs or 20
                                safe_rerun()
                            except Exception as e:
                                st.error(f"Failed to load surah: {e}")
                    idx += 1

        st.markdown('<div style="height:8px"></div>')

    # --- Tab 1: Reader ---
    with tab[1]:
        st.subheader("Quran Reader")
        # Controls row: surah, translation, transliteration toggle, font size
        ctrl_col1, ctrl_col2 = st.columns([3,2])
        with ctrl_col1:
            surah_num = st.number_input("Surah number", min_value=1, max_value=114, value=1, step=1)
            ayah_num = st.number_input("Ayah number (0 = full surah)", min_value=0, value=0)
        with ctrl_col2:
            # dynamic editions (fallback to built-in if API unreachable)
            try:
                editions = fetch_editions()
            except Exception:
                editions = ["en.sahih", "en.asad", "en.pickthall", "en.yusufali"]
            # determine default selection from user preference or fallback
            default_translation = user_default_translation if 'user_default_translation' in locals() else 'en.sahih'
            if default_translation in editions:
                default_idx = editions.index(default_translation)
            else:
                default_idx = 0
            translation = st.selectbox("Translation", editions, index=default_idx)
            show_translit = st.checkbox("Show transliteration (if available)", value=True)
        font_size = st.slider("Reading font size", min_value=16, max_value=32, value=20)
        ayahs_per_page = st.slider("Ayahs per page", min_value=5, max_value=40, value=20, step=1)

        cols = st.columns([3,1])
        with cols[0]:
            if st.button("Load Surah"):
                # Fetch Arabic original and translation (separately) and cache
                try:
                    ar_key = f"ar:{surah_num}"
                    tr_key = f"{translation}:{surah_num}"
                    if ar_key not in st.session_state.surah_cache:
                        st.session_state.surah_cache[ar_key] = fetch_surah(surah_num, "ar.alafasy")
                    if tr_key not in st.session_state.surah_cache:
                        st.session_state.surah_cache[tr_key] = fetch_surah(surah_num, translation)
                    st.session_state._current_surah_ar = st.session_state.surah_cache[ar_key].get('data', {})
                    st.session_state._current_surah_tr = st.session_state.surah_cache[tr_key].get('data', {})
                    # optional transliteration
                    if show_translit:
                        trl_key = f"en.transliteration:{surah_num}"
                        try:
                            st.session_state.surah_cache[trl_key] = fetch_surah(surah_num, "en.transliteration")
                        except Exception:
                            st.session_state.surah_cache[trl_key] = None
                        st.session_state._current_surah_trl = (st.session_state.surah_cache.get(trl_key) or {}).get('data')
                except Exception as e:
                    st.error(f"Error fetching surah: {e}")
                # reset pagination when loading a new surah
                st.session_state.surah_page = 0
                st.session_state.ayahs_per_page = ayahs_per_page
                # set current reciter url and mushaf mode from user prefs if available
                st.session_state.reciter_url = user_reciter_url if user_reciter_url else ''
                st.session_state.mushaf_mode = bool(user_mushaf_mode)

        with cols[1]:
            st.markdown("<div class='card'><div style='font-weight:700'>Quick Actions</div><div class='small' style='margin-top:0.5rem'>Log a verse, generate a short AI summary, or play audio for the selected ayah.</div></div>", unsafe_allow_html=True)

        # Display area
        if st.session_state.get('_current_surah_ar'):
            d_ar = st.session_state._current_surah_ar
            d_tr = st.session_state.get('_current_surah_tr', {}) or {}
            d_trl = st.session_state.get('_current_surah_trl')
            title = f"{d_tr.get('englishName', d_ar.get('englishName', ''))} — {d_ar.get('name')}"
            st.markdown(f"<h2 style='margin-bottom:0.05rem'>{title}</h2>", unsafe_allow_html=True)
            st.markdown(f"<div class='small'>Revelation: {d_ar.get('revelationType', '')} · Verses: {d_ar.get('numberOfAyahs', '')}</div>", unsafe_allow_html=True)

            # Styling for Arabic block (RTL, larger font)
            arabic_style = f"font-family: 'Scheherazade New', serif; font-size:{int(font_size*1.3)}px; direction:rtl; text-align:right; line-height:1.9;"
            trans_style = f"font-size:{int(font_size*0.95)}px; color:#4d4033; line-height:1.7;"
            trl_style = f"font-size:{int(font_size*0.85)}px; font-style:italic; color:#6b5b45;"

            if ayah_num and ayah_num > 0:
                ay_ar = next((a for a in d_ar.get('ayahs', []) if a.get('numberInSurah')==ayah_num), None)
                ay_tr = next((a for a in d_tr.get('ayahs', []) if a.get('numberInSurah')==ayah_num), None)
                ay_trl = None
                if d_trl:
                    ay_trl = next((a for a in d_trl.get('ayahs', []) if a.get('numberInSurah')==ayah_num), None)

                if ay_ar:
                    st.markdown(f"<div class='card' style='margin-top:0.8rem;padding:1.2rem'><div style='{arabic_style}'>{ay_ar.get('text')}</div><hr style='margin:0.6rem 0'><div style='{trans_style}'>{ay_tr.get('text') if ay_tr else ''}</div><div style='{trl_style}'>{ay_trl.get('text') if ay_trl else ''}</div></div>", unsafe_allow_html=True)
                    action_cols = st.columns([1,1,1])
                    with action_cols[0]:
                        if st.button("Log this ayah as read"):
                            add_log(user, surah_num, ayah_num)
                            award_if_eligible(user)
                            st.success("Logged — streak and points updated")
                    with action_cols[1]:
                        if st.button("Generate AI Summary"):
                            openai_key = st.secrets.get('OPENAI_API_KEY') if st.secrets and 'OPENAI_API_KEY' in st.secrets else os.environ.get('OPENAI_API_KEY')
                            summary = summarize_text(ay_ar.get('text'), openai_key=openai_key)
                            st.info(summary)
                    with action_cols[2]:
                        if st.button("Play Audio (TTS)"):
                            try:
                                # Try user-provided reciter URL first (supports placeholders {surah} and {ayah})
                                reciter = st.session_state.get('reciter_url', '') or (user_reciter_url or '')
                                audio_bytes = None
                                if reciter:
                                    try:
                                        audio_bytes = fetch_reciter_audio_by_url(reciter, surah_num, ayah_num)
                                    except Exception:
                                        audio_bytes = None
                                # Fallback to cached TTS
                                if not audio_bytes:
                                    audio_bytes = tts_cached_mp3_bytes(user, surah_num, ayah_num, ay_ar.get('text'), lang='ar')
                                st.audio(audio_bytes, format='audio/mp3')
                                b64 = base64.b64encode(audio_bytes).decode()
                                href = f"data:audio/mp3;base64,{b64}"
                                st.markdown(f"[Download audio]({href})")
                            except Exception as e:
                                st.error(f"Audio failed: {e}")
                else:
                    st.warning("Ayah not found in this surah.")
            else:
                # Full surah: show stacked Arabic + translation, paginated
                ayahs_ar = d_ar.get('ayahs', [])
                ayahs_tr = d_tr.get('ayahs', [])
                per_page = st.session_state.get('ayahs_per_page', 20)
                # Mushaf page mode: if enabled and ayah objects contain 'page', show by mushaf page
                mushaf_mode = st.session_state.get('mushaf_mode', False)
                handled_mushaf = False
                if mushaf_mode and any('page' in a for a in ayahs_ar):
                    pages = sorted(list({a.get('page') for a in ayahs_ar if a.get('page') is not None}))
                    if 'surah_page' not in st.session_state: st.session_state.surah_page = 0
                    # page index mapping
                    page_idx = st.selectbox('Mushaf page', options=pages, index=0)
                    # display ayahs that belong to selected mushaf page
                    sel_page = page_idx
                    page_ayahs = [a for a in ayahs_ar if a.get('page') == sel_page]
                    for a_ar in page_ayahs:
                        idx = a_ar.get('numberInSurah')
                        a_tr = next((x for x in ayahs_tr if x.get('numberInSurah')==idx), {})
                        trl_text = ''
                        if d_trl:
                            a_trl = next((x for x in d_trl.get('ayahs', []) if x.get('numberInSurah')==idx), {})
                            trl_text = a_trl.get('text','')
                        st.markdown(f"<div style='margin-bottom:0.9rem'><div style='{arabic_style}'>{a_ar.get('text')}</div><div style='{trans_style}'>{a_tr.get('text','')}</div><div style='{trl_style}'>{trl_text}</div><div class='small' style='margin-top:0.15rem'>Ayah {idx}</div></div>", unsafe_allow_html=True)
                    st.info(f"Showing mushaf page {sel_page} — {len(page_ayahs)} ayahs")
                    # mark handled so we skip standard pagination
                    handled_mushaf = True
                if not handled_mushaf:
                    if 'surah_page' not in st.session_state: st.session_state.surah_page = 0
                    max_page = max(0, (len(ayahs_ar)-1)//per_page)
                    nav_cols = st.columns([1,1,2])
                    with nav_cols[0]:
                        if st.button('Prev') and st.session_state.surah_page>0:
                            st.session_state.surah_page -= 1
                    with nav_cols[1]:
                        if st.button('Next') and st.session_state.surah_page<max_page:
                            st.session_state.surah_page += 1
                    with nav_cols[2]:
                        st.markdown(f"<div class='small' style='text-align:right'>Page <strong>{st.session_state.surah_page+1}</strong> of <strong>{max_page+1}</strong></div>", unsafe_allow_html=True)
                        go_page = st.number_input('Go to page', min_value=1, max_value=max_page+1, value=st.session_state.surah_page+1, key=f'go_page_{surah_num}')
                        if go_page-1 != st.session_state.surah_page:
                            st.session_state.surah_page = go_page-1
                    start = st.session_state.surah_page * per_page
                    end = start + per_page
                    for a_ar in ayahs_ar[start:end]:
                        idx = a_ar.get('numberInSurah')
                        a_tr = next((x for x in ayahs_tr if x.get('numberInSurah')==idx), {})
                        trl_text = ''
                        if d_trl:
                            a_trl = next((x for x in d_trl.get('ayahs', []) if x.get('numberInSurah')==idx), {})
                            trl_text = a_trl.get('text','')
                        st.markdown(f"<div style='margin-bottom:0.9rem'><div style='{arabic_style}'>{a_ar.get('text')}</div><div style='{trans_style}'>{a_tr.get('text','')}</div><div style='{trl_style}'>{trl_text}</div><div class='small' style='margin-top:0.15rem'>Ayah {idx}</div></div>", unsafe_allow_html=True)
                    st.info(f"Showing ayahs {start+1}–{min(end,len(ayahs_ar))} of {len(ayahs_ar)}. Use Next/Prev to navigate.")

    with tab[1]:
        st.subheader("My Progress")
        streak = compute_streak_for_user(user)
        total_reads = c.execute("SELECT COUNT(1) FROM reading_logs WHERE username=?", (user,)).fetchone()[0]
        st.markdown(f"<div class='card'><div style='font-weight:700'>Current streak: <span style='color:#6b5b45'>{streak} days</span></div><div class='small' style='margin-top:0.4rem'>Total verses read: {total_reads}</div></div>", unsafe_allow_html=True)
        st.markdown("<br>")
        logs = pd.read_sql_query("SELECT surah, ayah, created_date FROM reading_logs WHERE username=? ORDER BY created_date DESC LIMIT 20", conn, params=(user,))
        if not logs.empty:
            st.table(logs)
        else:
            st.info("No reading logs yet — read a verse to start building your streak.")

    with tab[2]:
        st.subheader("Achievements & Awards")
        rows = c.execute("SELECT key, unlocked_date FROM achievements WHERE username=?", (user,)).fetchall()
        # Show some default awards
        awards = [
            ("3_day_streak","3-day streak — Keep the habit"),
            ("7_day_streak","7-day streak — Golden Reader"),
        ]
        for k,label in awards:
            unlocked = any(r[0]==k for r in rows)
            if unlocked:
                d = next(r[1] for r in rows if r[0]==k)
                st.markdown(f"<div class='card' style='margin-bottom:0.5rem'><div style='font-weight:700'>{label} <span style='float:right' class='badge'>Unlocked</span></div><div class='small'>Unlocked on {d}</div></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='card' style='margin-bottom:0.5rem'><div style='font-weight:700'>{label} <span style='float:right' style='opacity:0.6'>Locked</span></div><div class='small'>Keep reading daily to unlock.</div></div>", unsafe_allow_html=True)

    with tab[3]:
        st.subheader("Settings & Secrets")
        st.markdown("<div class='small'>Add your OpenAI API key to Streamlit secrets or set environment variable <code>OPENAI_API_KEY</code> to enable AI summaries.</div>", unsafe_allow_html=True)
        if st.button("Sign Out"):
            st.session_state.logged_in = False
            st.session_state.user = None
            safe_rerun()

# --- Footer ---
st.markdown("<div style='text-align:center;margin-top:1.2rem;color:#8a7a66' class='small'>Al-Furqaan by Waleed · A gentle reader · Designed for web & mobile</div>", unsafe_allow_html=True)
