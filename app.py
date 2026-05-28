import streamlit as st
import firebase_admin
from firebase_admin import credentials, db
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import time
from datetime import datetime


# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = "https://aifarm-cd315-default-rtdb.asia-southeast1.firebasedatabase.app/"
DEVICE_PATH = "AIFARM01"

REFRESH_MS = 2000          # refresh หน้าเว็บทุก 2 วินาที
OFFLINE_SEC = 90           # ถ้าข้อมูลเก่ากว่า 90 วิ ถือว่า offline
PUMP_MAX_SEC = 60          # ส่งไปให้ STM32 ใช้เป็น max runtime ได้


# =========================================================
# STREAMLIT PAGE
# =========================================================

st.set_page_config(
    page_title="AIdi Smart Greenhouse",
    layout="wide",
    initial_sidebar_state="expanded"
)

st_autorefresh(interval=REFRESH_MS, key="auto_refresh")


# =========================================================
# FIREBASE INIT
# =========================================================

@st.cache_resource
def init_firebase():
    """
    Init Firebase แค่ครั้งเดียว
    ห้าม delete_app ทุก rerun เพราะจะทำให้ช้าและเสี่ยง error
    """
    if not firebase_admin._apps:
        key_dict = dict(st.secrets["firebase"])
        key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred, {
            "databaseURL": DATABASE_URL
        })

    return db.reference(DEVICE_PATH)


root_ref = init_firebase()


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def pick(data, *keys, default="--"):
    """
    Дึงค่าจาก dict โดยรองรับหลายชื่อ key
    เช่น air_humi / air_humid / air_humidity
    """
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value

    return default


def to_float(value):
    try:
        return float(value)
    except Exception:
        return None


def fmt(value, unit="", digits=1):
    """
    format ค่า sensor ให้ไม่พังถ้าเป็น None หรือไม่มีข้อมูล
    """
    if value == "--" or value is None:
        return "--"

    try:
        num = float(value)
        return f"{num:.{digits}f} {unit}".strip()
    except Exception:
        return f"{value} {unit}".strip()


def get_epoch_from_current(current):
    """
    รองรับ timestamp หลายชื่อ
    """
    ts = pick(
        current,
        "ts",
        "timestamp",
        "unix",
        "unix_time",
        "epoch",
        "time",
        default=None
    )

    try:
        ts = int(float(ts))

        # ถ้าเป็น millisecond ให้แปลงเป็น second
        if ts > 10_000_000_000:
            ts = ts // 1000

        return ts
    except Exception:
        return None


def make_command(pump_value):
    """
    สร้าง command ที่มี request_id กันบอร์ดแยกคำสั่งเก่า/ใหม่ได้
    """
    now = int(time.time())

    return {
        "pump": int(pump_value),
        "source": "dashboard",
        "ts": now,
        "request_id": f"pump_{int(pump_value)}_{now}",
        "max_duration_sec": PUMP_MAX_SEC
    }


def load_history_dataframe(history_data):
    """
    แปลง AIFARM01/history เป็น DataFrame สำหรับทำกราฟ
    รองรับ history ที่เป็น dict
    """
    if not isinstance(history_data, dict) or len(history_data) == 0:
        return pd.DataFrame()

    rows = []

    for key, item in history_data.items():
        if not isinstance(item, dict):
            continue

        ts = get_epoch_from_current(item)

        # ถ้าไม่มี ts ลองใช้ key เป็นเวลา
        dt = None
        if ts:
            dt = datetime.fromtimestamp(ts)
        else:
            # รองรับ key แนว 2026-05-28_12-30-00
            try:
                safe_key = str(key).replace("_", " ")
                dt = pd.to_datetime(safe_key, errors="coerce")
            except Exception:
                dt = None

        if dt is None or pd.isna(dt):
            continue

        rows.append({
            "time": dt,
            "air_temp": to_float(pick(item, "air_temp", "airTemp")),
            "air_humi": to_float(pick(item, "air_humi", "air_humid", "air_humidity")),
            "soil_temp": to_float(pick(item, "soil_temp", "soilTemp")),
            "soil_humi": to_float(pick(item, "soil_humi", "soil_humid", "soil_moisture")),
            "soil_ec": to_float(pick(item, "soil_ec", "EC", "ec")),
            "soil_ph": to_float(pick(item, "soil_ph", "ph", "pH")),
            "n": to_float(pick(item, "n", "N", "soil_n")),
            "p": to_float(pick(item, "p", "P", "soil_p")),
            "k": to_float(pick(item, "k", "K", "soil_k")),
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values("time")
    df = df.drop_duplicates(subset=["time"], keep="last")
    df = df.set_index("time")

    return df


def status_badge(label, value):
    """
    แสดงสถานะอุปกรณ์แบบอ่านง่าย
    """
    if str(value) in ["1", "ON", "on", "true", "True"]:
        st.success(f"{label}: ON")
    elif str(value) in ["0", "OFF", "off", "false", "False"]:
        st.error(f"{label}: OFF")
    else:
        st.info(f"{label}: {value}")


# =========================================================
# READ FIREBASE (จุดที่แก้ไขหลักอยู่ตรงนี้ครับ)
# =========================================================

try:
    all_data = root_ref.get() or {}
    # วิ่งไปดึงข้อมูลที่โฟลเดอร์ควบคุมของบอร์ด STM32 โดยตรง
    stm32_control = db.reference("devices/stm32f410/control").get() or {}
except Exception as e:
    st.error(f"Firebase read error: {e}")
    st.stop()

current = all_data.get("current", {}) if isinstance(all_data, dict) else {}
history = all_data.get("history", {}) if isinstance(all_data, dict) else {}

db_mode = all_data.get("control_mode", "auto")
if db_mode not in ["auto", "manual"]:
    db_mode = "auto"

# ประกอบร่างข้อมูลสถานะอุปกรณ์ (cmd) จากโฟลเดอร์จริงของบอร์ด
pump_data = stm32_control.get("pump", {}) if isinstance(stm32_control, dict) else {}
cmd = {
    "pump": pump_data.get("pump", "N/A"),
    "request_id": pump_data.get("request_id", "N/A"),
    "source": pump_data.get("source", "N/A"),
    "fan": stm32_control.get("fan", "N/A")
}


# =========================================================
# HEADER
# =========================================================

st.title("AIdi - Dashboard สมาร์ทฟาร์ม")
st.caption("หน้าควบคุมและตรวจสอบสถานะโรงเรือนอัจฉริยะจาก Firebase Realtime Database")

readable_string = all_data.get("AIFARM01_readable", None)
if readable_string:
    st.success(f"สถานะภาพรวม: {readable_string}")


# =========================================================
# ONLINE / OFFLINE STATUS
# =========================================================

last_ts = get_epoch_from_current(current)
now_ts = int(time.time())

top1, top2, top3, top4 = st.columns(4)

with top1:
    if last_ts:
        age = now_ts - last_ts

        if age <= OFFLINE_SEC:
            st.success(f"Online")
            st.caption(f"ข้อมูลล่าสุด {age} วินาทีที่แล้ว")
        else:
            st.error("Offline / ข้อมูลค้าง")
            st.caption(f"ข้อมูลเก่า {age} วินาที")
    else:
        st.warning("ไม่พบ timestamp")
        st.caption("ควรให้ STM32 ส่ง ts มาด้วย")

with top2:
    st.metric("โหมดใน Firebase", db_mode.upper())

with top3:
    st.metric("Device Path", DEVICE_PATH)

with top4:
    st.metric("Refresh", f"{REFRESH_MS // 1000}s")


st.write("---")


# =========================================================
# CONTROL PANEL
# =========================================================

st.markdown("### แผงควบคุมระบบ")

mode_options = ["auto", "manual"]
mode_labels = {
    "auto": "โหมดอัตโนมัติ (AI & Schedule)",
    "manual": "โหมดสั่งการเอง (Manual)"
}

selected_mode = st.radio(
    "เลือกโหมดการทำงาน",
    mode_options,
    index=mode_options.index(db_mode),
    format_func=lambda x: mode_labels[x],
    horizontal=True
)

# เขียน Firebase เฉพาะตอน mode เปลี่ยนจริง
if selected_mode != db_mode:
    try:
        root_ref.child("control_mode").set(selected_mode)
        st.toast(f"เปลี่ยนโหมดเป็น {selected_mode.upper()} แล้ว")
        st.rerun()
    except Exception as e:
        st.error(f"เปลี่ยนโหมดไม่สำเร็จ: {e}")

if selected_mode == "manual":
    st.warning("ตอนนี้ Manual Mode: AI และ Schedule ควรถูกพักฝั่ง STM32 ด้วย")

    c_btn1, c_btn2, c_btn3 = st.columns([1, 1, 2])

    with c_btn1:
        if st.button("เปิดปั๊มน้ำ", type="primary", use_container_width=True):
            try:
                db.reference("devices/stm32f410/control/pump").set(make_command(1))
                st.toast("ส่งคำสั่งเปิดปั๊มน้ำไปที่บอร์ด STM32 แล้ว")
            except Exception as e:
                st.error(f"ส่งคำสั่งไม่สำเร็จ: {e}")

    with c_btn2:
        if st.button("ปิดปั๊มน้ำ", use_container_width=True):
            try:
                db.reference("devices/stm32f410/control/pump").set(make_command(0))
                st.toast("ส่งคำสั่งปิดปั๊มน้ำไปที่บอร์ด STM32 แล้ว")
            except Exception as e:
                st.error(f"ส่งคำสั่งไม่สำเร็จ: {e}")

    with c_btn3:
        st.caption(
            f"คำสั่ง manual จะส่ง request_id และ max_duration_sec={PUMP_MAX_SEC} "
            "เพื่อให้ STM32 กันคำสั่งซ้ำและกันปั๊มค้าง"
        )

else:
    st.info("Auto Mode: ระบบควบคุมโดย AI / Schedule / Logic ฝั่ง STM32")


st.write("---")


# =========================================================
# SENSOR METRICS
# =========================================================

st.markdown("### สภาพแวดล้อมปัจจุบันจาก STM32")

air_temp = pick(current, "air_temp", "airTemp")
air_humi = pick(current, "air_humi", "air_humid", "air_humidity")
soil_temp = pick(current, "soil_temp", "soilTemp")
soil_humi = pick(current, "soil_humi", "soil_humid", "soil_moisture")

soil_ec = pick(current, "soil_ec", "EC", "ec")
soil_ph = pick(current, "soil_ph", "ph", "pH")
soil_n = pick(current, "n", "N", "soil_n")
soil_p = pick(current, "p", "P", "soil_p")
soil_k = pick(current, "k", "K", "soil_k")

st.markdown("#### อากาศและความชื้นดิน")
m1, m2, m3, m4 = st.columns(4)

with m1:
    st.metric("อุณหภูมิอากาศ", fmt(air_temp, "°C", 1))
with m2:
    st.metric("ความชื้นอากาศ", fmt(air_humi, "%", 1))
with m3:
    st.metric("อุณหภูมิดิน", fmt(soil_temp, "°C", 1))
with m4:
    st.metric("ความชื้นดิน", fmt(soil_humi, "%", 1))

st.markdown("#### คุณภาพและธาตุอาหารในดิน")
m5, m6, m7, m8, m9 = st.columns(5)

with m5:
    st.metric("EC", fmt(soil_ec, "µS/cm", 0))
with m6:
    st.metric("pH", fmt(soil_ph, "", 2))
with m7:
    st.metric("N", fmt(soil_n, "mg/kg", 0))
with m8:
    st.metric("P", fmt(soil_p, "mg/kg", 0))
with m9:
    st.metric("K", fmt(soil_k, "mg/kg", 0))


# =========================================================
# FORECAST
# =========================================================

st.markdown("### ข้อมูลพยากรณ์อากาศ")

forecast_temp = pick(current, "tmd_forecast_temp", "forecast_temp")
forecast_humi = pick(current, "tmd_forecast_humid", "tmd_forecast_humi", "forecast_humi")

f1, f2, f3 = st.columns([1, 1, 3])

with f1:
    st.metric("พยากรณ์อุณหภูมิ", fmt(forecast_temp, "°C", 1))
with f2:
    st.metric("พยากรณ์ความชื้น", fmt(forecast_humi, "%", 1))
with f3:
    st.caption("ถ้ายังไม่มีค่า ให้ตรวจ path ใน Firebase ว่าใช้ชื่อ key ตรงกับโค้ดหรือไม่")


# =========================================================
# DEVICE STATUS
# =========================================================

st.markdown("### สถานะอุปกรณ์ควบคุม")

s1, s2, s3, s4 = st.columns(4)

with s1:
    status_badge("พัดลม", pick(cmd, "fan", default="N/A"))

with s2:
    status_badge("ปั๊มน้ำ", pick(cmd, "pump", default="N/A"))

with s3:
    st.info(f"request_id: {pick(cmd, 'request_id', default='N/A')}")

with s4:
    st.info(f"source: {pick(cmd, 'source', default='N/A')}")

current_text = all_data.get("current_text", "OK")
st.caption(f"ข้อความระบบ: {current_text}")


st.write("---")


# =========================================================
# CHARTS
# =========================================================

st.markdown("### กราฟข้อมูลย้อนหลัง")

df = load_history_dataframe(history)

if df.empty:
    st.warning(
        "ยังไม่มีข้อมูล history หรือรูปแบบข้อมูลยังไม่ตรง "
        "ถ้าจะให้กราฟขึ้น ต้องมี path เช่น AIFARM01/history พร้อม timestamp"
    )
else:
    graph_group = st.selectbox(
        "เลือกกลุ่มกราฟ",
        [
            "อากาศและความชื้น",
            "ดินและ pH/EC",
            "ธาตุอาหาร NPK"
        ]
    )

    if graph_group == "อากาศและความชื้น":
        cols = ["air_temp", "air_humi", "soil_temp", "soil_humi"]
    elif graph_group == "ดินและ pH/EC":
        cols = ["soil_ec", "soil_ph"]
    else:
        cols = ["n", "p", "k"]

    show_cols = [c for c in cols if c in df.columns and df[c].notna().any()]

    if show_cols:
        st.line_chart(df[show_cols])
    else:
        st.warning("มี history แต่ไม่มี key ที่ใช้วาดกราฟในกลุ่มนี้")

    with st.expander("ดูตาราง history ล่าสุด"):
        st.dataframe(df.tail(50), use_container_width=True)


# =========================================================
# RAW DATA DEBUG
# =========================================================

st.write("---")
st.markdown("### Raw Data / Debug")

tab1, tab2, tab3, tab4 = st.tabs([
    "Current",
    "Command",
    "History",
    "All Data"
])

with tab1:
    st.json(current)

with tab2:
    st.json(cmd)

with tab3:
    if isinstance(history, dict):
        st.write(f"จำนวน record ใน history: {len(history)}")
    st.json(history)

with tab4:
    st.json(all_data)