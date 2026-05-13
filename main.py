from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timedelta
import io

# PDF + graf
import matplotlib.pyplot as plt
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
app = FastAPI()
#-------------------------------------------------------------------------------------------------------------------
from fastapi import Request, Form
from fastapi.responses import RedirectResponse

SECRET_USER = "admin"
SECRET_PASS = "1234"

sessions = set()

@app.post("/auth/login")
def login():
    return {"token": "demo"}
# ========================
# DB
# ========================
DATABASE_URL = "sqlite:///./data.db"
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'data.db')}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# ========================
# MODELY
# ========================
class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    device_uid = Column(String, unique=True)
    temperature_limit = Column(Float, default=8.0)
    alert_delay_minutes = Column(Integer, default=10)
    api_key = Column(String, unique=True)
    alert_active_since = Column(DateTime, nullable=True)

    measurements = relationship("MeasurementDB", back_populates="device")


class MeasurementDB(Base):
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True)
    temperature = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    device_id = Column(Integer, ForeignKey("devices.id"))
    device = relationship("Device", back_populates="measurements")


Base.metadata.create_all(bind=engine)

# ========================
# REQUEST MODEL
# ========================
class Measurement(BaseModel):
    device_id: str
    temperature: float
    api_key: str

# ========================
# RECEIVE DATA
# ========================
@app.post("/api/measurements")
def receive_data(data: Measurement):
    db = SessionLocal()

    device = db.query(Device).filter(Device.device_uid == data.device_id).first()

    if not device:
        db.close()
        return {"error": "device not registered"}
        
        

    if device.api_key != data.api_key:
        db.close()
        return {"error": "Invalid API key"}

    measurement = MeasurementDB(
        temperature=data.temperature,
        device_id=device.id
    )
    db.add(measurement)

    alert = False

    if data.temperature > device.temperature_limit:
        if device.alert_active_since is None:
            device.alert_active_since = datetime.utcnow()
        else:
            delta = datetime.utcnow() - device.alert_active_since
            if delta.total_seconds() >= device.alert_delay_minutes * 60:
                alert = True
                print(f"🚨 ALARM: {device.device_uid}")
    else:
        device.alert_active_since = None

    db.commit()
    db.close()

    return {"status": "ok", "alert": alert}

# ========================
import secrets

class RegisterRequest(BaseModel):
    device_id: str
    temperature_limit: float = 8.0


@app.post("/api/register")
def register_device(data: RegisterRequest):
    db = SessionLocal()

    device = db.query(Device).filter(Device.device_uid == data.device_id).first()

    if device:
        db.close()
        return {
            "device_id": device.device_uid,
            "api_key": device.api_key,
            "limit": device.temperature_limit,
            "status": "already_exists"
        }

    new_device = Device(
        device_uid=data.device_id,
        temperature_limit=data.temperature_limit,
        api_key=secrets.token_hex(16)
    )

    db.add(new_device)
    db.commit()
    db.refresh(new_device)
    db.close()

    return {
        "device_id": new_device.device_uid,
        "api_key": new_device.api_key,
        "limit": new_device.temperature_limit,
        "status": "created"
    }
# DATA
# ========================
@app.get("/api/data/{device_uid}")
def get_data(device_uid: str):
    db = SessionLocal()

    device = db.query(Device).filter(Device.device_uid == device_uid).first()
    if not device:
        db.close()
        return []

    measurements = db.query(MeasurementDB)\
        .filter(MeasurementDB.device_id == device.id)\
        .order_by(MeasurementDB.created_at)\
        .all()

    db.close()

    return [
        {"temperature": m.temperature, "time": m.created_at}
        for m in measurements
    ]

# ========================
# DEVICES
# ========================

@app.get("/api/devices_list")
def devices_list():
    db = SessionLocal()
    devices = db.query(Device.device_uid).all()
    db.close()

    return [d[0] for d in devices]
#---------------

@app.get("/admin/device/{device_uid}", response_class=HTMLResponse)
def device_detail(device_uid: str):
    db = SessionLocal()

    device = db.query(Device).filter(Device.device_uid == device_uid).first()
    if not device:
        db.close()
        return "Device not found"

    data = db.query(MeasurementDB)\
        .filter(MeasurementDB.device_id == device.id)\
        .order_by(MeasurementDB.created_at.desc())\
        .limit(50)\
        .all()

    db.close()

    rows = ""
    for d in data:
        rows += f"<tr><td>{d.created_at}</td><td>{d.temperature}</td></tr>"

    return f"""
    <html>
    <body style="font-family:Arial;background:#0b1220;color:white;padding:20px">

    <h2>📟 Device: {device.device_uid}</h2>
    <p>Limit: {device.temperature_limit} °C</p>
    <p>API KEY: {device.api_key}</p>

    <h3>📊 Poslední měření</h3>

    <table border="1" style="color:white;width:100%">
        <tr><th>Time</th><th>Temp</th></tr>
        {rows}
    </table>

    <br>
    <a href="/admin" style="color:#3b82f6">← zpět</a>

    </body>
    </html>
    """
# ========================
# PDF REPORT
# ========================
import tempfile
from fastapi.responses import FileResponse
@app.get("/api/report/{device_uid}")
def report(device_uid: str):
    db = SessionLocal()

    device = db.query(Device).filter(Device.device_uid == device_uid).first()
    if not device:
        db.close()
        return {"error": "device not found"}

    since = datetime.utcnow() - timedelta(days=30)

    measurements = db.query(MeasurementDB)\
        .filter(MeasurementDB.device_id == device.id)\
        .filter(MeasurementDB.created_at >= since)\
        .order_by(MeasurementDB.created_at)\
        .all()

    db.close()

    if not measurements:
        return {"error": "no data"}

    # ======================
    # DATA ANALYTICS
    # ======================
    temps = [m.temperature for m in measurements]

    avg_temp = sum(temps) / len(temps)
    min_temp = min(temps)
    max_temp = max(temps)

    alerts = [t for t in temps if t > device.temperature_limit]

    alert_count = len(alerts)
    total = len(temps)

    compliance = ((total - alert_count) / total) * 100

    # ======================
    # PDF SETUP
    # ======================
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    elements = []

    # ======================
    # HEADER
    # ======================
    elements.append(Paragraph("HACCP TEMPERATURE MONITORING REPORT", styles["Title"]))
    elements.append(Paragraph("Food Safety Compliance Document", styles["Normal"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(f"Device ID: {device_uid}", styles["Normal"]))
    elements.append(Paragraph(f"Reporting period: Last 30 days", styles["Normal"]))
    elements.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%d.%m.%Y %H:%M')}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # ======================
    # SUMMARY BOX
    # ======================
    elements.append(Paragraph("SUMMARY", styles["Heading2"]))

    elements.append(Paragraph(f"Average temperature: <b>{avg_temp:.2f} °C</b>", styles["Normal"]))
    elements.append(Paragraph(f"Min temperature: <b>{min_temp:.2f} °C</b>", styles["Normal"]))
    elements.append(Paragraph(f"Max temperature: <b>{max_temp:.2f} °C</b>", styles["Normal"]))
    elements.append(Paragraph(f"Configured limit: <b>{device.temperature_limit} °C</b>", styles["Normal"]))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph(f"Total measurements: {total}", styles["Normal"]))
    elements.append(Paragraph(f"Limit violations: <b>{alert_count}</b>", styles["Normal"]))
    elements.append(Paragraph(f"Compliance rate: <b>{compliance:.2f}%</b>", styles["Normal"]))

    elements.append(Spacer(1, 12))

    # ======================
    # STATUS
    # ======================
    status = "COMPLIANT"
    if alert_count > 0:
        status = "NON-COMPLIANT"

    elements.append(Paragraph(f"STATUS: {status}", styles["Heading2"]))

    # ======================
    # TABLE (sampled data)
    # ======================
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("SAMPLE LOG DATA", styles["Heading2"]))

    table_data = [["Time", "Temperature (°C)", "Status"]]

    step = max(1, len(measurements)//60)

    for i in range(0, len(measurements), step):
        m = measurements[i]
        t = m.temperature

        state = "OK"
        if t > device.temperature_limit:
            state = "ALERT"

        table_data.append([
            m.created_at.strftime("%d.%m %H:%M"),
            f"{t:.2f}",
            state
        ])

    table = Table(table_data)
    table.setStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
    ])

    elements.append(table)

    # ======================
    # FOOTER NOTE
    # ======================
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        "This document is automatically generated by IoT HACCP Monitoring System.",
        styles["Normal"]
    ))

    # ======================
    # BUILD PDF
    # ======================
    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=HACCP_{device_uid}.pdf"
        }
    )

# ========================
# DASHBOARD (MOBILE)
# ========================
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Temp Monitor</title>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
body{
    margin:0;
    font-family: Inter, Arial;
    background:#0b1220;
    color:white;
}

/* top bar */
.topbar{
    padding:15px;
    font-size:18px;
    font-weight:600;
}

/* layout */
.container{
    max-width:1000px;
    margin:auto;
    padding:15px;
}

/* cards */
.grid{
    display:grid;
    grid-template-columns: 1fr;
    gap:15px;
}

@media(min-width:900px){
    .grid{
        grid-template-columns: 1fr 1fr;
    }
}

.card{
    background:#111a2e;
    border-radius:16px;
    padding:16px;
    box-shadow:0 10px 25px rgba(0,0,0,0.3);
}

/* big temperature */
.temp{
    font-size:48px;
    font-weight:700;
    margin:10px 0;
}

.status{
    font-size:16px;
    opacity:0.9;
}

.ok{color:#22c55e;}
.bad{color:#ef4444;}

/* select */
select{
    width:100%;
    padding:12px;
    border-radius:10px;
    border:none;
    background:#0f172a;
    color:white;
    font-size:16px;
    margin-top:10px;
}

/* button */
button{
    width:100%;
    padding:12px;
    border:none;
    border-radius:10px;
    background:#2563eb;
    color:white;
    font-size:16px;
    margin-top:10px;
}

/* chart */
canvas{
    width:100% !important;
    height:300px !important;
}
</style>
</head>

<body>

<div class="topbar">
🌡️ IoT Teplotní Monitoring
</div>

<div class="container">

<div class="grid">

<!-- LEVÝ SLOUPEC -->
<div>

    <div class="card">
        <h3>Zařízení</h3>

        <select id="deviceSelect"></select>

        <div class="temp" id="temp">-- °C</div>
        <div class="status" id="status">Načítání...</div>

        <button onclick="downloadPDF()">
            📄 Report (PDF)
        </button>
    </div>

    <div class="card">
        <h3>📈 Statistiky</h3>

        <p>🌡️ Min: <span id="minTemp">-</span> °C</p>
        <p>🌡️ Max: <span id="maxTemp">-</span> °C</p>
        <p>📊 Průměr: <span id="avgTemp">-</span> °C</p>
        <p>📦 Počet měření: <span id="countTemp">-</span></p>
    </div>

    <div class="card">
        <h3>🚨 Alarmy</h3>

        <p>Překročení limitu:
            <span id="alertCount">0</span>
        </p>

        <p>Limit:
            <span id="deviceLimit">-</span> °C
        </p>
    </div>

</div>

<!-- PRAVÝ SLOUPEC -->
<div>

    <div class="card">
        <h3>📊 Celkový graf</h3>
        <canvas id="chart"></canvas>
    </div>

    <div class="card">
        <h3>📅 Dnešní teploty</h3>
        <canvas id="dayChart"></canvas>
    </div>

    <div class="card">
        <h3>🗓️ Měsíční přehled</h3>
        <canvas id="monthChart"></canvas>
    </div>

</div>

</div>
</div>

<script>
let chart;
let avgChart;
let alarmChart;
let dayChart;
let monthChart;

async function loadDevices(){

    let res = await fetch('/api/devices_list');
    let data = await res.json();

    let select = document.getElementById("deviceSelect");

    select.innerHTML = "";

    for (const d of data) {

        select.innerHTML += `
            <option value="${d}">${d}</option>
        `;
    }

    // automaticky vyber první zařízení
    if (data.length > 0) {
        select.value = data[0];
        loadData(data[0]);
    }
}



async function loadData(dev){

    let res = await fetch('/api/data/' + dev);
    let data = await res.json();

    let limit = (await (await fetch('/api/debug/device/' + dev)).json()).limit;

    let labels = data.map(d => new Date(d.time).toLocaleString());    let temps = data.map(d => d.temperature);
    let avgTemps = [];
    let alarms = [];

    for(let i=0;i<temps.length;i++){

        let subset = temps.slice(
            Math.max(0, i-5),
            i+1
        );

        let avg =
            subset.reduce((a,b)=>a+b,0)
            / subset.length;

        avgTemps.push(avg);

        alarms.push(
            temps[i] > limit ? 1 : 0
        );
    }
    let last = temps[temps.length - 1] || 0;
    // =====================
// Statistiky
// =====================

let min = Math.min(...temps);
let max = Math.max(...temps);

let avg =
    temps.reduce((a,b)=>a+b,0) / temps.length;

document.getElementById("minTemp").innerText =
    min.toFixed(1);

document.getElementById("maxTemp").innerText =
    max.toFixed(1);

document.getElementById("avgTemp").innerText =
    avg.toFixed(1);

document.getElementById("countTemp").innerText =
    temps.length;

// =====================
// Alarmy
// =====================

let alertCount =
    temps.filter(t => t > limit).length;

document.getElementById("alertCount").innerText =
    alertCount;

document.getElementById("deviceLimit").innerText =
    limit;

    document.getElementById("temp").innerText = last.toFixed(1) + " °C";

    let status = document.getElementById("status");

    if(last > limit){
        status.innerHTML = "🚨 VYSOKÁ TEPLOTA";
        status.className = "status bad";
        document.body.style.background = "#1a0b0b";
    } else {
        status.innerHTML = "✅ V normě";
        status.className = "status ok";
        document.body.style.background = "#0b1220";
    }

    if(chart) chart.destroy();

if(avgChart) avgChart.destroy();
if(alarmChart) alarmChart.destroy();

chart = new Chart(document.getElementById('chart'),{
    type:'line',
    data:{
        labels:labels,
        datasets:[{
            label:'Teplota',
            data:temps,
            borderColor:'#3b82f6',
            tension:0.3
        }]
    },
    options:{
        responsive:true
    }
});

avgChart = new Chart(
    document.getElementById('avgChart'),
    {
        type:'line',

        data:{
            labels:labels,

            datasets:[{
                label:'Průměr',

                data:avgTemps,

                borderColor:'#22c55e',

                tension:0.3
            }]
        },

        options:{
            responsive:true
        }
    }
);

alarmChart = new Chart(
    document.getElementById('alarmChart'),
    {
        type:'bar',

        data:{
            labels:labels,

            datasets:[{
                label:'Alarm',

                data:alarms
            }]
        },

        options:{
            responsive:true
        }
    }
);

function downloadPDF(){
    let dev = document.getElementById('deviceSelect').value;
    window.open('/api/report/' + dev);
}

document.getElementById('deviceSelect').addEventListener('change',e=>{
    loadData(e.target.value);
});

loadDevices();

setInterval(()=>{
    let dev = document.getElementById('deviceSelect').value;
    if(dev) loadData(dev);
},5000);
// =====================
// DNEŠNÍ GRAF
// =====================

let today = new Date().toISOString().slice(0,10);

let dayData = data.filter(d =>
    d.time.startsWith(today)
);

let dayLabels = dayData.map(d => d.time.slice(11,16));
let dayTemps = dayData.map(d => d.temperature);

if(dayChart) dayChart.destroy();

dayChart = new Chart(
    document.getElementById('dayChart'),
    {
        type:'line',

        data:{
            labels:dayLabels,

            datasets:[{
                label:'Dnes',
                data:dayTemps,
                borderColor:'#22c55e',
                tension:0.3
            }]
        },

        options:{
            responsive:true
        }
    }
);

// =====================
// MĚSÍČNÍ GRAF
// =====================

let grouped = {};

data.forEach(d => {

    let day = d.time.slice(0,10);

    if(!grouped[day]){
        grouped[day] = [];
    }

    grouped[day].push(d.temperature);
});

let monthLabels = Object.keys(grouped);

let monthTemps = monthLabels.map(day => {

    let arr = grouped[day];

    return arr.reduce((a,b)=>a+b,0) / arr.length;
});

if(monthChart) monthChart.destroy();

monthChart = new Chart(
    document.getElementById('monthChart'),
    {
        type:'bar',

        data:{
            labels:monthLabels,

            datasets:[{
                label:'Denní průměr',
                data:monthTemps,
                backgroundColor:'#f59e0b'
            }]
        },

        options:{
            responsive:true
        }
    }
);
</script>

</body>
</html>
"""

# ========================
# ========================
# ========================
# LOGIN PAGE
# ========================

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <html>
    <body style="font-family:Arial;background:#0b1220;color:white;padding:20px">

        <h2>🔐 Login</h2>

        <form method="post" action="/login">

            <input name="user" placeholder="user">
            <br><br>

            <input
                name="password"
                type="password"
                placeholder="password"
            >
            <br><br>

            <button type="submit">
                Login
            </button>

        </form>

    </body>
    </html>
    """


# ========================
# LOGIN ACTION
# ========================

@app.post("/login")
def login(user: str = Form(...), password: str = Form(...)):

    if user == SECRET_USER and password == SECRET_PASS:
        sessions.add(user)

        return RedirectResponse(
            "/admin",
            status_code=302
        )

    return {"error": "wrong credentials"}


# ========================
# ADMIN PANEL
# ========================

@app.get("/admin", response_class=HTMLResponse)
def admin_panel():

    if "admin" not in sessions:
        return RedirectResponse("/login")

    return """
<!DOCTYPE html>

<html>

<head>

    <title>IoT Admin</title>

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <style>

        body{
            font-family:Arial;
            background:#0b1220;
            color:white;
            padding:20px;
        }

        input, button{
            padding:10px;
            margin:5px;
            width:100%;
        }

        .card{
            background:#111a2e;
            padding:15px;
            margin:10px 0;
            border-radius:10px;
        }

        button{
            background:#2563eb;
            color:white;
            border:none;
            border-radius:8px;
        }

        a{
            color:#3b82f6;
        }

    </style>

</head>

<body>

<h2>📡 IoT Admin Panel</h2>

<div class="card">

    <h3>➕ Přidat zařízení</h3>

    <input
        id="device"
        placeholder="device_id"
    >

    <input
        id="limit"
        placeholder="temperature limit"
        value="8"
    >

    <button onclick="addDevice()">
        Přidat
    </button>

</div>

<div class="card">

    <h3>📋 Zařízení</h3>

    <div id="list"></div>

</div>

<script>

async function loadDevices(){

    let res = await fetch('/api/devices_list');
    let data = await res.json();

    let html = "";

    for (const d of data) {

        let debug = await fetch('/api/debug/device/' + d);
        let info = await debug.json();

        html += `
        <div class="card">
            <h3>📟 ${d}</h3>

            <input id="name_${d}" value="${d}">
            <input id="limit_${d}" value="${info.limit}" type="number" step="0.1">

            <button onclick="saveDevice('${d}')">💾 Uložit změny</button>
            <button onclick="deleteDevice('${d}')">🗑️ Smazat</button>

            <br><br>
            <a href="/admin/device/${encodeURIComponent(d)}">Detail</a>
        </div>
        `;
    }

    document.getElementById("list").innerHTML = html;
}



async function addDevice(){

    let id = document.getElementById("device").value;

    let limit = parseFloat(
        document.getElementById("limit").value
    );

    try {
        let res = await fetch("/api/register", {
            method:"POST",
            headers:{
                "Content-Type":"application/json"
            },
            body: JSON.stringify({
                device_id: id,
                temperature_limit: limit
            })
        });

        let data = await res.json();

        console.log("REGISTER RESPONSE:", data);

        if(!res.ok){
            alert("ERROR: " + JSON.stringify(data));
            return;
        }

        alert("API KEY: " + data.api_key);

        loadDevices();

    } catch(err){
        console.error(err);
        alert("Network error");
    }
}

async function saveDevice(oldId){

    let newId = document.getElementById(
        "name_" + oldId
    ).value;

    let limit = parseFloat(
        document.getElementById(
            "limit_" + oldId
        ).value
    );

    let res = await fetch(
        '/api/update-device/' + oldId,
        {
            method:'POST',

            headers:{
                'Content-Type':'application/json'
            },

            body: JSON.stringify({
                new_device_id: newId,
                temperature_limit: limit
            })
        }
    );

    let data = await res.json();

    if(data.status === "updated"){
        alert("Uloženo");
        loadDevices();
    } else {
        alert(data.error);
    }
}
async function deleteDevice(id){

    if(!confirm("Opravdu smazat zařízení?")){
        return;
    }

    let res = await fetch(
        '/api/delete-device/' + id
    );

    let data = await res.json();

    if(data.status === "deleted"){
        alert("Zařízení smazáno");
        loadDevices();
    } else {
        alert(data.error);
    }
}
loadDevices();
setInterval(loadDevices, 20000);
</script>

</body>
</html>
"""

#===============================================
# =========================================================
# UPDATE DEVICE
# =========================================================

class UpdateDeviceRequest(BaseModel):
    new_device_id: str
    temperature_limit: float


    
@app.post("/api/update-device/{device_uid}")
def update_device(device_uid: str, data: UpdateDeviceRequest):

    db = SessionLocal()

    device = db.query(Device).filter(
        Device.device_uid == device_uid
    ).first()

    if not device:
        db.close()
        return {"error": "device not found"}

    # kontrola duplicity názvu
    existing = db.query(Device).filter(
        Device.device_uid == data.new_device_id
    ).first()

    if existing and existing.id != device.id:
        db.close()
        return {"error": "device name already exists"}

    device.device_uid = data.new_device_id
    device.temperature_limit = data.temperature_limit

    db.commit()
    db.close()

    return {"status": "updated"}
#======================================
# =========================================================
# DELETE DEVICE
# =========================================================

@app.get("/api/delete-device/{device_uid}")
def delete_device(device_uid: str):

    db = SessionLocal()

    device = db.query(Device).filter(
        Device.device_uid == device_uid
    ).first()

    if not device:
        db.close()
        return {"error": "device not found"}

    db.query(MeasurementDB).filter(
        MeasurementDB.device_id == device.id
    ).delete()

    db.delete(device)

    db.commit()
    db.close()

    return {"status": "deleted"}
@app.get("/api/debug/device/{device_uid}")
def debug_device(device_uid: str):
    db = SessionLocal()

    device = db.query(Device).filter(
        Device.device_uid == device_uid
    ).first()

    if not device:
        db.close()
        return {"error": "not found"}

    result = {
        "device_id": device.device_uid,
        "api_key": device.api_key,
        "limit": device.temperature_limit
    }

    db.close()

    return result
    #==================

#====================================

#======================================================
@app.get("/api/clear-all")
def clear_all():
    db = SessionLocal()

    deleted = db.query(MeasurementDB).delete()

    db.commit()
    db.close()

    return {"deleted_records": deleted}
#========================================================
@app.get("/api/set-key/{device_uid}/{new_key}")
def set_key(device_uid: str, new_key: str):
    db = SessionLocal()

    device = db.query(Device).filter(
        Device.device_uid == device_uid
    ).first()

    if not device:
        db.close()
        return {"error": "device not found"}

    device.api_key = new_key

    db.commit()
    db.close()

    return {
        "status": "updated",
        "device": device_uid,
        "new_key": new_key
    }

#=================================================================
@app.get("/")
def root():
    return {"message": "Server běží 🚀"}