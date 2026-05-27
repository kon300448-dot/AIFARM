import streamlit as st
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
import time

# 1. ตั้งค่าหน้าเว็บให้กว้าง
st.set_page_config(page_title="AIdi Smart Greenhouse", layout="wide")

# 2. เคลียร์ Cache ของ Firebase (กันบั๊กตอน Rerun)
if firebase_admin._apps:
    for app in list(firebase_admin._apps.values()):
        firebase_admin.delete_app(app)

import json

# ... โค้ดส่วนบน (เคลียร์ Cache) เหมือนเดิม ...

# 3. เชื่อมต่อ Firebase
if not firebase_admin._apps:
    key_dict = dict(st.secrets["firebase"])
    
    # --- เพิ่มบรรทัดนี้เข้าไปครับ (สำคัญมาก!) ---
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
    # ----------------------------------------
    
    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://aifarm-cd315-default-rtdb.asia-southeast1.firebasedatabase.app/'
    })

st.title("🌱 AIdi - Dashboard สมาร์ทฟาร์ม")
st.markdown("หน้าปัดควบคุมและตรวจสอบสถานะโรงเรือนอัจฉริยะแบบ Real-time")

placeholder = st.empty()

while True:
    with placeholder.container():
        try:
            ref = db.reference('AIFARM01')
            all_data = ref.get()

            if all_data:
                # -- แถบประกาศสถานะสรุป (อ่านง่ายๆ) --
                readable_string = all_data.get('AIFARM01_readable', 'ไม่มีข้อมูลสรุปข้อความ')
                st.success(f"📌 **สถานะภาพรวม:** {readable_string}")
                
                # เข้าถึงข้อมูลเซนเซอร์ (สมมติว่าอยู่ใน Node 'current')
                # ถ้าโครงสร้างชื่อตัวแปรเปลี่ยนไป สามารถแก้ชื่อในเครื่องหมายคำพูดได้เลย
                current = all_data.get('current', {})
                cmd = all_data.get('last_command_request', {})

                # --- โซนที่ 1: เกจวัดตัวเลขสภาพแวดล้อม (ดูง่ายตาแตก) ---
                st.markdown("### 🌡️ สภาพแวดล้อมปัจจุบัน")
                col1, col2, col3, col4, col5 = st.columns(5)
                
                with col1:
                    st.metric(label="อุณหภูมิอากาศ", value=f"{current.get('air_temp', '--')} °C")
                with col2:
                    st.metric(label="ความชื้นอากาศ", value=f"{current.get('air_humid', '--')} %")
                with col3:
                    st.metric(label="อุณหภูมิดิน", value=f"{current.get('soil_temp', '--')} °C")
                with col4:
                    st.metric(label="ความชื้นดิน", value=f"{current.get('soil_humid', '--')} %")
                with col5:
                    st.metric(label="ค่า pH", value=f"{current.get('ph', '--')}")

                # --- โซนที่ 2: สถานะการควบคุมอุปกรณ์ ---
                st.markdown("### ⚙️ สถานะอุปกรณ์ควบคุม")
                c1, c2, c3 = st.columns(3)
                
                with c1:
                    # สมมติว่าดึงสถานะพัดลมมาโชว์
                    st.info(f"💨 **พัดลม (Fan):** {cmd.get('fan', 'N/A')}")
                with c2:
                    # สมมติว่าดึงสถานะปั๊มน้ำมาโชว์
                    st.info(f"💧 **ปั๊มน้ำ (Pump):** {cmd.get('pump', 'N/A')}")
                with c3:
                    st.info(f"📝 **ข้อความระบบ:** {all_data.get('current_text', 'OK')}")

                st.write("---")

                # --- โซนที่ 3: แท็บซ่อนข้อมูลดิบ (สำหรับโปรแกรมเมอร์ไว้ดูข้อมูลครบๆ) ---
                st.markdown("#### 🔍 ข้อมูลเชิงลึก (Raw Data)")
                tab1, tab2, tab3 = st.tabs(["📦 ข้อมูลเซนเซอร์ (Current)", "🛠️ ประวัติคำสั่ง (Command)", "🗄️ ฐานข้อมูลทั้งหมด"])
                
                with tab1:
                    st.json(current)
                with tab2:
                    st.json(cmd)
                with tab3:
                    st.json(all_data)

            else:
                st.warning("⚠️ กำลังรอข้อมูลจาก Firebase...")

        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาด: {e}")

    # หน่วงเวลา 2 วินาที (ปรับให้เร็วกว่านี้ได้ถ้าอยากให้เรียลไทม์จัดๆ)
    time.sleep(2)