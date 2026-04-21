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

@app.post("/auth/login")
def login():
    return {"token": "demo"}
# ========================
# DB
# ========================
DATABASE_URL = "sqlite:///./data.db"

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
        import secrets
        device = Device(
            device_uid=data.device_id,
            api_key=secrets.token_hex(16)
        )
        db.add(device)
        db.commit()
        db.refresh(device)

        print("NEW DEVICE:", device.device_uid)
        print("API KEY:", device.api_key)

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
user_id = Column(Integer, ForeignKey("users.id"))
@app.get("/api/devices_list")
def devices_list():
    db = SessionLocal()
    devices = db.query(Device).all()
    db.close()

    return [
        {
            "id": d.device_uid,
            "limit": d.temperature_limit,
            "api_key": d.api_key
        }
        for d in devices
    ]

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

<div class="card">
    <h3>Zařízení</h3>
    <select id="deviceSelect"></select>

    <div class="temp" id="temp">-- °C</div>
    <div class="status" id="status">Načítání...</div>

    <button onclick="downloadPDF()">📄 Report (PDF)</button>
</div>

<div class="card">
    <h3>Graf vývoje</h3>
    <canvas id="chart"></canvas>
</div>

</div>

</div>

<script>
let chart;

async function loadDevices(){
    let res = await fetch('/api/devices_list');
    let devices = await res.json();

    let sel = document.getElementById('deviceSelect');
    sel.innerHTML="";

    devices.forEach(d=>{
        let o=document.createElement("option");
        o.value=d;
        o.text=d;
        sel.appendChild(o);
    });

    if(devices.length) loadData(devices[0]);
}

async function loadData(dev){

    let res = await fetch('/api/data/' + dev);
    let data = await res.json();

    let limit = (await (await fetch('/api/device/' + dev)).json()).limit;

    let labels = data.map(d => d.time);
    let temps = data.map(d => d.temperature);

    let last = temps[temps.length - 1] || 0;

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
            responsive:true,
            plugins:{
                legend:{display:false}
            }
        }
    });
}

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
},10000);

</script>

</body>
</html>
"""
# ========================
# ROOT
# ========================
from fastapi.responses import HTMLResponse

@app.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>IoT Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial; background:#0b1220; color:white; padding:20px; }
        input, button { padding:10px; margin:5px; width:100%; }
        .card { background:#111a2e; padding:15px; margin:10px 0; border-radius:10px; }
        button { background:#2563eb; color:white; border:none; border-radius:8px; }
    </style>
</head>
<body>

<h2>📡 IoT Admin Panel</h2>

<div class="card">
    <h3>➕ Přidat zařízení</h3>
    <input id="device" placeholder="device_id">
    <input id="limit" placeholder="temperature limit" value="8">
    <button onclick="addDevice()">Přidat</button>
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
    data.forEach(d=>{
        html += "<div>📟 " + d + "</div>";
    });

    document.getElementById("list").innerHTML = html;
}

async function addDevice(){
    let id = document.getElementById("device").value;
    let limit = document.getElementById("limit").value;

    let res = await fetch("/api/register", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
            device_id: id,
            temperature_limit: parseFloat(limit)
        })
    });

    let data = await res.json();
    alert("API KEY: " + data.api_key);

    loadDevices();
}

loadDevices();

</script>

</body>
</html>
"""
#===============================================
import secrets

class RegisterRequest(BaseModel):
    device_id: str
    temperature_limit: float = 8.0

@app.post("/api/register")
def register_device(data: RegisterRequest):
    db = SessionLocal()

    # kontrola existence
    existing = db.query(Device).filter(Device.device_uid == data.device_id).first()
    if existing:
        db.close()
        return {
            "error": "device already exists",
            "api_key": existing.api_key
        }

    # vytvoření nového zařízení
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
        "api_key": new_device.api_key
    }
    #=================================================================
@app.get("/")
def root():
    return {"message": "Server běží 🚀"}