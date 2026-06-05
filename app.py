import streamlit as st
import firebase_admin
from firebase_admin import credentials, db
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import time
from datetime import datetime, timezone, timedelta
import re

# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = "https://aifarm-cd315-default-rtdb.asia-southeast1.firebasedatabase.app/"
DEVICE_PATH = "AIFARM01"

REFRESH_MS = 2000
OFFLINE_SEC = 90
PUMP_MAX_SEC = 60

# --- Config สำหรับ Backend V9 (Command Queue) ---
USE_COMMAND_QUEUE = True

TH_TZ = timezone(timedelta(hours=7))


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
    if value == "--" or value is None:
        return "--"

    try:
        num = float(value)
        return f"{num:.{digits}f} {unit}".strip()
    except Exception:
        return f"{value} {unit}".strip()


def get_epoch_from_data(data):
    ts = pick(
        data,
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

        if ts > 10_000_000_000:
            ts = ts // 1000

        return ts
    except Exception:
        return None


def now_th_hhmm():
    return datetime.now(TH_TZ).strftime("%H:%M")


def now_th_text():
    return datetime.now(TH_TZ).strftime("%Y-%m-%d %H:%M:%S")


def make_command(action):
    """
    สำหรับคำสั่ง Manual on/off (ใช้โครงสร้างเดิม)
    """
    now_ms = int(time.time() * 1000)
    now_sec = now_ms // 1000

    if action == "on":
        pump_value = 1
        duration = PUMP_MAX_SEC
        force_off = False
    elif action == "off":
        pump_value = 0
        duration = 1
        force_off = True
    else:
        raise ValueError("action ต้องเป็น on หรือ off เท่านั้น")

    return {
        "id": f"manual_{action}_{now_ms}",
        "command_type": "manual_relay",
        "source": "dashboard",
        "relay": 1,
        "device": "pump",
        "action": action,
        "pump": pump_value,
        "force_off": force_off,
        "time": now_th_hhmm(),
        "duration_sec": duration,
        "backend_processed": False,
        "backend_processed_ts": None,
        "backend_status": "waiting",
        "ts": now_sec,
        "ts_ms": now_ms,
        "created_at_th": now_th_text()
    }


def make_schedule_command(hhmm, duration_sec, relay):
    """
    สำหรับคำสั่งสร้างตารางเวลา (Schedule WT)
    """
    now_ms = int(time.time() * 1000)
    now_sec = now_ms // 1000
    safe_hhmm = hhmm.replace(":", "")
    
    return {
        "id": f"schedule_{relay}_{safe_hhmm}_{now_ms}",
        "command_type": "schedule_relay",
        "source": "dashboard",
        "relay": int(relay),
        "device": "pump",
        "time": hhmm,
        "duration_sec": int(duration_sec),
        "backend_processed": False,
        "backend_processed_ts": None,
        "backend_status": "waiting",
        "ts": now_sec,
        "ts_ms": now_ms,
        "created_at_th": now_th_text()
    }


def load_history_dataframe(history_data):
    if not isinstance(history_data, dict) or len(history_data) == 0:
        return pd.DataFrame()

    rows = []

    # วนลูปชั้นที่ 1: เข้าถึงโฟลเดอร์วันที่ (เช่น "2026-06-05")
    for date_key, date_folder in history_data.items():
        if not isinstance(date_folder, dict):
            continue
        
        # วนลูปชั้นที่ 2: เข้าถึงก้อนข้อมูลแต่ละช่วงเวลาในวันนั้น (เช่น Push ID หรือเวลา "13-43-57")
        for record_key, item in date_folder.items():
            if not isinstance(item, dict):
                continue

            # พยายามหาค่า Timestamp จากใน item ก่อน
            ts = get_epoch_from_data(item)
            dt = None

            if ts:
                dt = datetime.fromtimestamp(ts)
            else:
                # ถ้าไม่มี Timestamp ให้ลองเอา "วันที่" มารวมกับ "ชื่อคีย์" 
                # (เผื่อ STM32 ส่งชื่อคีย์มาเป็นเวลา เช่น "13-43-57")
                try:
                    time_str = str(record_key).replace("-", ":").replace("_", ":")
                    datetime_str = f"{date_key} {time_str}"
                    dt = pd.to_datetime(datetime_str, errors="coerce")
                except Exception:
                    dt = None

            # ถ้าแปลงเป็นเวลาไม่ได้เลย ให้ข้ามข้อมูลก้อนนี้ไป
            if dt is None or pd.isna(dt):
                continue

            # ดึงข้อมูลเซนเซอร์ออกมา (ถ้าชื่อ Key ใน Firebase เป็นแบบอื่น ให้เติมชื่อลงไปใน "..." ได้เลย)
            rows.append({
                "time": dt,
                "air_temp": to_float(pick(item, "air_temp", "airTemp", "Air_Temp", "air")),
                "air_humi": to_float(pick(item, "air_humi", "air_humid", "air_humidity")),
                "soil_temp": to_float(pick(item, "soil_temp", "soilTemp", "Soil_Temp")),
                "soil_humi": to_float(pick(item, "soil_humi", "soil_humid", "soil_moisture", "Soil_Moisture")),
                "soil_ec": to_float(pick(item, "soil_ec", "EC", "ec")),
                "soil_ph": to_float(pick(item, "soil_ph", "ph", "pH")),
                "n": to_float(pick(item, "n", "N", "soil_n")),
                "p": to_float(pick(item, "p", "P", "soil_p")),
                "k": to_float(pick(item, "k", "K", "soil_k")),
            })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # เรียงลำดับตามเวลา และตัดข้อมูลที่ซ้ำซ้อนทิ้ง
    df = df.sort_values("time")
    df = df.drop_duplicates(subset=["time"], keep="last")
    df = df.set_index("time")

    return df

def status_badge(label, value):
    value_str = str(value)

    if value_str in ["1", "ON", "on", "true", "True"]:
        st.success(f"{label}: ON")
    elif value_str in ["0", "OFF", "off", "false", "False"]:
        st.error(f"{label}: OFF")
    else:
        st.info(f"{label}: {value}")


def backend_status_box(cmd):
    processed = cmd.get("backend_processed", None)
    status = cmd.get("backend_status", None)

    if processed is True:
        st.success("backend: Processed")
    elif processed is False:
        st.warning("backend: Waiting")
    else:
        st.info("backend: N/A")

    if status:
        st.caption(f"status: {status}")


try:
    all_data = root_ref.get() or {}
except Exception as e:
    st.error(f"Firebase read error: {e}")
    st.stop()

current = all_data.get("current", {}) if isinstance(all_data, dict) else {}
cmd = all_data.get("last_command_request", {}) if isinstance(all_data, dict) else {}

# ---------------------------------------------------------
# [แก้ไขใหม่] กวาดหา Key ที่เป็นรูปแบบวันที่ (เช่น 2026-06-05) มาทำเป็น history
# ---------------------------------------------------------
history = {}
if isinstance(all_data, dict):
    for key, val in all_data.items():
        if re.match(r"^\d{4}-\d{2}-\d{2}$", str(key)):
            history[key] = val
# ---------------------------------------------------------

relay_state = all_data.get("relay_state", {}) if isinstance(all_data, dict) else {}

# ดึง path Queue สำหรับ Backend V9
command_queue = all_data.get("command_queue", {}) if isinstance(all_data, dict) else {}
last_command_sent = all_data.get("last_command_sent", {}) if isinstance(all_data, dict) else {}
last_command_ack = all_data.get("last_command_ack", {}) if isinstance(all_data, dict) else {}

# นับจำนวนคิวที่ตกค้าง (ยังไม่ได้ process)
pending_count = 0
if isinstance(command_queue, dict):
    for q_id, q_data in command_queue.items():
        if isinstance(q_data, dict) and not q_data.get("backend_processed", False):
            pending_count += 1

db_mode = all_data.get("control_mode", "auto")
if db_mode not in ["auto", "manual"]:
    db_mode = "auto"


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

last_ts = get_epoch_from_data(current)
now_ts = int(time.time())

top1, top2, top3, top4 = st.columns(4)

with top1:
    if last_ts:
        age = now_ts - last_ts

        if age <= OFFLINE_SEC:
            st.success("Online")
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

if selected_mode != db_mode:
    try:
        root_ref.child("control_mode").set(selected_mode)
        st.toast(f"เปลี่ยนโหมดเป็น {selected_mode.upper()} แล้ว")
        st.rerun()
    except Exception as e:
        st.error(f"เปลี่ยนโหมดไม่สำเร็จ: {e}")

if selected_mode == "manual":
    st.warning("Manual Mode: ฝั่ง backend/STM32 ต้องหยุด AI และ Schedule เองด้วย")

    c_btn1, c_btn2, c_btn3 = st.columns([1, 1, 2])

    with c_btn1:
        if st.button("เปิดปั๊มน้ำ", type="primary", use_container_width=True):
            try:
                command = make_command("on")
                if USE_COMMAND_QUEUE:
                    root_ref.child("command_queue").push(command)
                    st.toast("เพิ่มคำสั่งเปิดปั๊มน้ำเข้าคิวแล้ว")
                else:
                    root_ref.child("last_command_request").set(command)
                    st.toast("ส่งคำสั่งเปิดปั๊มน้ำแล้ว")
                st.rerun()
            except Exception as e:
                st.error(f"ส่งคำสั่งไม่สำเร็จ: {e}")

    with c_btn2:
        if st.button("ปิดปั๊มน้ำ", use_container_width=True):
            try:
                command = make_command("off")
                if USE_COMMAND_QUEUE:
                    root_ref.child("command_queue").push(command)
                    st.toast("เพิ่มคำสั่งปิดปั๊มน้ำเข้าคิวแล้ว")
                else:
                    root_ref.child("last_command_request").set(command)
                    st.toast("ส่งคำสั่งปิดปั๊มน้ำแล้ว")
                st.rerun()
            except Exception as e:
                st.error(f"ส่งคำสั่งไม่สำเร็จ: {e}")

    with c_btn3:
        queue_status_text = "ใช้งาน Command Queue (Push)" if USE_COMMAND_QUEUE else "ใช้งานแบบเดิม (Set: last_command_request)"
        st.caption(
            f"ระบบ: {queue_status_text} | "
            "คำสั่ง OFF ส่ง action='off' และ force_off=True"
        )

else:
    st.info("Auto Mode: ระบบควบคุมโดย AI / Schedule / Logic ฝั่ง STM32")


st.write("---")
# =========================================================
# SCHEDULE WATER PUMP (ฟีเจอร์ใหม่)
# =========================================================
st.markdown("### ⏰ ตั้งเวลาเปิด/ปิดปั๊มน้ำ")
st.caption("ตั้งเวลาส่งเข้า Queue เพื่อให้ Backend แปลงเป็นคำสั่งตารางเวลา (WT) ให้ STM32")

t_col1, t_col2, t_col3, t_col4 = st.columns([1, 1, 1, 1])

with t_col1:
    # เติม step=60 เพื่อให้ปรับเวลาได้ละเอียดทีละ 1 นาที
    schedule_time = st.time_input("เวลาเปิดปั๊มน้ำ", step=60)

with t_col2:
    duration_sec = st.number_input("ระยะเวลา (วินาที)", min_value=1, max_value=999, value=120)

with t_col3:
    relay_sel = st.selectbox("Relay", [1, 2, 3, 4], index=0)

with t_col4:
    st.write("") # ดันปุ่มลงมาให้ตรงกับช่องกรอกข้อมูล
    st.write("")
    if st.button("เพิ่มตารางเปิดน้ำ", type="primary", use_container_width=True):
        try:
            hhmm = schedule_time.strftime("%H:%M")
            schedule_cmd = make_schedule_command(hhmm, duration_sec, relay_sel)
            
            # ส่งเข้า Queue อย่างเดียว ห้าม Set ทับ
            root_ref.child("command_queue").push(schedule_cmd)
            st.toast(f"เพิ่มตาราง Relay {relay_sel} เวลา {hhmm} นาน {duration_sec} วินาทีแล้ว")
            
            # หน่วงเวลาเล็กน้อยให้ Firebase ประมวลผลก่อนโหลดหน้าใหม่
            time.sleep(0.5) 
            st.rerun()
        except Exception as e:
            st.error(f"เกิดข้อผิดพลาดในการบันทึกตาราง: {e}")

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

# สถานะจริง ควรมาจาก AIFARM01/relay_state
real_fan = pick(relay_state, "fan", default="N/A")
real_pump = pick(relay_state, "pump", default="N/A")

# fallback ถ้ายังไม่มี relay_state ให้โชว์จาก command แต่ระบุว่าเป็นคำสั่งล่าสุด
if real_fan == "N/A":
    real_fan = pick(cmd, "fan", default="N/A")

if real_pump == "N/A":
    real_pump = pick(cmd, "pump", default="N/A")

with s1:
    status_badge("พัดลม", real_fan)

with s2:
    status_badge("ปั๊มน้ำ", real_pump)

with s3:
    backend_status_box(cmd)

with s4:
    st.info(f"action: {pick(cmd, 'action', default='N/A')}")
    st.caption(f"duration: {pick(cmd, 'duration_sec', default='N/A')}s")

# แสดงข้อมูล Queue และ ACK ที่ดึงมาใหม่
last_cmd_action = pick(last_command_sent, "action", default="N/A")
if last_cmd_action == "N/A": 
    # ลองดึงเวลาถ้าเป็นคำสั่ง Schedule WT
    last_cmd_action = pick(last_command_sent, "time", default="N/A") 

last_ack_event = pick(last_command_ack, "event", default="N/A")
st.caption(f"📌 Queue pending: {pending_count} | last sent: {last_cmd_action} | ack: {last_ack_event}")

cmd_id = pick(cmd, "id", default="N/A")
cmd_source = pick(cmd, "source", default="N/A")
cmd_created = pick(cmd, "created_at_th", default="N/A")

st.caption(f"คำสั่ง Manual ล่าสุด: {cmd_id} | source: {cmd_source} | created: {cmd_created}")

relay_updated_ts = get_epoch_from_data(relay_state)
if relay_updated_ts:
    relay_age = now_ts - relay_updated_ts
    st.caption(f"relay_state อัปเดตล่าสุด {relay_age} วินาทีที่แล้ว")

current_text = all_data.get("current_text", "OK")
st.caption(f"ข้อความระบบ: {current_text}")


st.write("---")


# =========================================================
# CHARTS
# =================
# =========================================================
# CHARTS (ต่อจาก # ================= บรรทัดสุดท้ายของคุณได้เลย)
# =========================================================

st.markdown("### 📊 แนวโน้มข้อมูลสภาพแวดล้อมย้อนหลัง")

def load_history_dataframe(history_data):
    if not isinstance(history_data, dict) or len(history_data) == 0:
        return pd.DataFrame()

    rows = []

    # วนลูปชั้นที่ 1: เข้าถึงโฟลเดอร์วันที่ (เช่น "2026-06-05")
    for date_key, date_folder in history_data.items():
        if not isinstance(date_folder, dict):
            continue
        
        # วนลูปชั้นที่ 2: เข้าถึงก้อนข้อมูลแต่ละช่วงเวลาในวันนั้น (เช่น Push ID หรือเวลา "13-43-57")
        for record_key, item in date_folder.items():
            if not isinstance(item, dict):
                continue

            # พยายามหาค่า Timestamp จากใน item ก่อน
            ts = get_epoch_from_data(item)
            dt = None

            if ts:
                dt = datetime.fromtimestamp(ts)
            else:
                # ถ้าไม่มี Timestamp ให้ลองเอา "วันที่" มารวมกับ "ชื่อคีย์" 
                # (เผื่อ STM32 ส่งชื่อคีย์มาเป็นเวลา เช่น "13-43-57")
                try:
                    time_str = str(record_key).replace("-", ":").replace("_", ":")
                    datetime_str = f"{date_key} {time_str}"
                    dt = pd.to_datetime(datetime_str, errors="coerce")
                except Exception:
                    dt = None

            # ถ้าแปลงเป็นเวลาไม่ได้เลย ให้ข้ามข้อมูลก้อนนี้ไป
            if dt is None or pd.isna(dt):
                continue

            # ดึงข้อมูลเซนเซอร์ออกมา (ถ้าชื่อ Key ใน Firebase เป็นแบบอื่น ให้เติมชื่อลงไปใน "..." ได้เลย)
            rows.append({
                "time": dt,
                "air_temp": to_float(pick(item, "air_temp", "airTemp", "Air_Temp", "air")),
                "air_humi": to_float(pick(item, "air_humi", "air_humid", "air_humidity")),
                "soil_temp": to_float(pick(item, "soil_temp", "soilTemp", "Soil_Temp")),
                "soil_humi": to_float(pick(item, "soil_humi", "soil_humid", "soil_moisture", "Soil_Moisture")),
                "soil_ec": to_float(pick(item, "soil_ec", "EC", "ec")),
                "soil_ph": to_float(pick(item, "soil_ph", "ph", "pH")),
                "n": to_float(pick(item, "n", "N", "soil_n")),
                "p": to_float(pick(item, "p", "P", "soil_p")),
                "k": to_float(pick(item, "k", "K", "soil_k")),
            })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # เรียงลำดับตามเวลา และตัดข้อมูลที่ซ้ำซ้อนทิ้ง
    df = df.sort_values("time")
    df = df.drop_duplicates(subset=["time"], keep="last")
    df = df.set_index("time")

    return df