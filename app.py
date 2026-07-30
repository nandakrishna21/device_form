import json
import os
import threading
import time
from flask import Flask, render_template, request, flash, redirect, url_for, session, jsonify, abort
import pyodbc
import subprocess
import platform
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = 'essl-device-form-secret-key-2026'

DB_CONFIG = {
    'server': r'localhost\SQLEXPRESS',
    'database': 'eTimetracklite1',
    'driver': '{SQL Server}'
}

LOGIN_PASSWORD = 'admin123'

TRACKING_USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'attendance_dashboard', 'users.json')

def load_tracking_users():
    users_path = os.path.abspath(TRACKING_USERS_FILE)
    if os.path.exists(users_path):
        with open(users_path, 'r') as f:
            return json.load(f)
    return {}

TRACKING_USERS = load_tracking_users()

def get_conn():
    return pyodbc.connect(
        f'DRIVER={DB_CONFIG["driver"]};'
        f'SERVER={DB_CONFIG["server"]};'
        f'DATABASE={DB_CONFIG["database"]};'
        f'Trusted_Connection=yes;'
    )

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return wrapper

def ping_device(ip):
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    try:
        result = subprocess.run(['ping', param, '1', ip],
                                capture_output=True, timeout=5, text=True)
        return result.returncode == 0
    except:
        return False

def get_device_attendance_status():
    """Return dict {device_id: True} for devices that are Online.
    A device is Online if it has recent punches (last 7 days) OR
    the ESSL server confirms it's connected (LastPing within 10 min)."""
    conn = get_conn()
    cursor = conn.cursor()
    active = {}

    week_ago = datetime.now() - timedelta(days=7)
    for tbl in ['DeviceLogs', f'DeviceLogs_{datetime.now().month}_{datetime.now().year}']:
        try:
            cursor.execute(f"SELECT DISTINCT DeviceId FROM {tbl} WHERE LogDate >= ?", (week_ago,))
            for row in cursor.fetchall():
                active[row[0]] = True
        except Exception:
            pass

    ping_cutoff = datetime.now() - timedelta(minutes=10)
    try:
        cursor.execute("SELECT DeviceId FROM Devices WHERE LastPing IS NOT NULL AND LastPing >= ?", (ping_cutoff,))
        for row in cursor.fetchall():
            if row[0] not in active:
                active[row[0]] = True
    except Exception:
        pass

    cursor.close()
    conn.close()
    return active

def get_all_devices():
    cutoff = datetime.now() - timedelta(minutes=1)
    attend_active = get_device_attendance_status()
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DeviceId, DeviceFName, DeviceSName, SerialNumber, IpAddress, DeviceDirection,
               Timezone, DeviceLocation, TransactionStamp, OpStamp, LastPing, DeviceType
        FROM Devices ORDER BY DeviceFName
    """)
    columns = [desc[0] for desc in cursor.description]
    rows = []
    for row in cursor.fetchall():
        d = dict(zip(columns, row))
        lp = d.get('LastPing')
        last_ping_ok = lp is not None and lp.year > 1900
        d['is_online'] = attend_active.get(d['DeviceId'], False)
        d['last_ping_ago'] = None
        if last_ping_ok:
            delta = datetime.now() - lp
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            if mins > 0:
                d['last_ping_ago'] = f'{mins}m {secs}s ago'
            else:
                d['last_ping_ago'] = f'{secs}s ago'
        rows.append(d)
    cursor.close()
    conn.close()
    return rows

def get_device_by_id(device_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Devices WHERE DeviceId = ?", (device_id,))
    cols = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return dict(zip(cols, row))
    return None

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == LOGIN_PASSWORD:
            session['logged_in'] = True
            next_page = request.args.get('next') or url_for('form')
            return redirect(next_page)
        else:
            error = 'Incorrect password'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    return redirect(url_for('form'))

@app.route('/form', methods=['GET', 'POST'])
@login_required
def form():
    if request.method == 'POST':
        device_fname = request.form.get('DeviceFName', '').strip()
        device_sname = request.form.get('DeviceSName', '').strip()
        serial_number = request.form.get('SerialNumber', '').strip()
        ip_address = '108.181.175.250'
        device_location = request.form.get('DeviceLocation', '').strip()
        timezone = request.form.get('Timezone', '330').strip()
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if not all([device_fname, serial_number]):
            if is_ajax:
                return jsonify({'error': 'Device Name and Serial Number are required'}), 400
            flash('Device Name and Serial Number are required', 'error')
            return redirect(url_for('form'))

        if len(serial_number) != 13:
            if is_ajax:
                return jsonify({'error': f'Serial number must be exactly 13 characters (got {len(serial_number)})'}), 400
            flash(f'Serial Number must be exactly 13 characters (got {len(serial_number)})', 'error')
            return redirect(url_for('form'))

        try:
            conn = get_conn()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Devices WHERE SerialNumber = ?", (serial_number,))
            if cursor.fetchone()[0] > 0:
                cursor.close()
                conn.close()
                if is_ajax:
                    return jsonify({'error': f'Serial Number "{serial_number}" already exists'}), 409
                flash(f'Duplicate: Serial Number "{serial_number}" already exists', 'error')
                return redirect(url_for('form'))

            cursor.execute("""
                INSERT INTO Devices (DeviceFName, DeviceSName, SerialNumber, IpAddress,
                    DeviceLocation, Timezone, ConnectionType, DeviceDirection, DeviceType,
                    BaudRate, CommKey, FaceDeviceType, DownLoadType, DeviceActivationCode, OpStamp, LastPing)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (device_fname, device_sname, serial_number, ip_address,
                  device_location, timezone, 'Tcp/IP', 'altinout', 'Attendance',
                  '115200', '0', 'Normal', 1, 0, 0, '1900-01-01 00:00:00'))
            conn.commit()

            cursor.execute("SELECT DeviceId FROM Devices WHERE SerialNumber = ?", (serial_number,))
            new_id = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            if is_ajax:
                return jsonify({
                    'success': True,
                    'device_id': new_id,
                    'name': device_fname,
                    'serial': serial_number,
                    'is_online': False
                })

            flash(f'"{device_fname}" added — device is OFFLINE (waiting for first connection)', 'warning')

        except pyodbc.Error as e:
            if is_ajax:
                return jsonify({'error': f'Database error: {e}'}), 500
            flash(f'Database error: {e}', 'error')

        return redirect(url_for('form'))

    return render_template('form.html', DB_CONFIG=DB_CONFIG)

@app.route('/devices')
@login_required
def device_list():
    devices = get_all_devices()
    return render_template('devices.html', devices=devices, DB_CONFIG=DB_CONFIG)

@app.route('/edit/<int:device_id>', methods=['GET', 'POST'])
@login_required
def edit_device(device_id):
    device = get_device_by_id(device_id)
    if not device:
        flash('Device not found', 'error')
        return redirect(url_for('device_list'))

    if request.method == 'POST':
        device_fname = request.form.get('DeviceFName', '').strip()
        serial_number = request.form.get('SerialNumber', '').strip()
        ip_address = request.form.get('IpAddress', '').strip()
        device_location = request.form.get('DeviceLocation', '').strip()
        timezone = request.form.get('Timezone', '330').strip()

        if not all([device_fname, serial_number, ip_address]):
            flash('Device Name, Serial Number, and IP Address are required', 'error')
            return redirect(url_for('edit_device', device_id=device_id))

        try:
            conn = get_conn()
            cursor = conn.cursor()

            if serial_number != (device.get('SerialNumber') or ''):
                cursor.execute("SELECT COUNT(*) FROM Devices WHERE SerialNumber = ? AND DeviceId != ?",
                               (serial_number, device_id))
                if cursor.fetchone()[0] > 0:
                    flash(f'Duplicate Serial Number "{serial_number}" belongs to another device', 'error')
                    cursor.close()
                    conn.close()
                    return redirect(url_for('edit_device', device_id=device_id))

            cursor.execute("""
                UPDATE Devices SET DeviceFName=?, SerialNumber=?, IpAddress=?,
                    DeviceLocation=?, Timezone=?
                WHERE DeviceId=?
            """, (device_fname, serial_number, ip_address, device_location, timezone, device_id))
            conn.commit()
            cursor.close()
            conn.close()

            device_online = ping_device(ip_address)
            if device_online:
                flash(f'"{device_fname}" updated — device is ONLINE', 'success')
            else:
                flash(f'"{device_fname}" updated — device is OFFLINE (ping failed)', 'warning')

            return redirect(url_for('device_list'))
        except pyodbc.Error as e:
            flash(f'Update error: {e}', 'error')

    return render_template('edit.html', device=device, DB_CONFIG=DB_CONFIG, now=datetime.now, timedelta=timedelta)

@app.route('/delete/<int:device_id>', methods=['POST'])
@login_required
def delete_device(device_id):
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Devices WHERE DeviceId = ?", (device_id,))
        conn.commit()
        cursor.close()
        conn.close()
        flash(f'Device ID {device_id} deleted', 'success')
    except pyodbc.Error as e:
        flash(f'Delete error: {e}', 'error')
    return redirect(url_for('device_list'))

@app.route('/api/devices/status')
@login_required
def api_device_status():
    devices = get_all_devices()
    data = []
    for d in devices:
        data.append({
            'id': d['DeviceId'],
            'name': d['DeviceFName'],
            'serial': d['SerialNumber'],
            'ip': d['IpAddress'],
            'location': d.get('DeviceLocation') or '',
            'device_type': d.get('DeviceType') or '',
            'direction': d.get('DeviceDirection') or '',
            'timezone': d.get('Timezone') or '',
            'is_online': d['is_online'],
            'last_ping_ago': d['last_ping_ago'],
            'last_ping': str(d['LastPing']) if d.get('LastPing') and d['LastPing'].year > 1900 else None,
        })
    return jsonify(data)

@app.route('/api/device/<int:device_id>/status')
def api_device_ping_status(device_id):
    device = get_device_by_id(device_id)
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    attend_active = get_device_attendance_status()
    is_online = attend_active.get(device_id, False)
    lp = device.get('LastPing')
    last_ping_ago = None
    if lp is not None and lp.year > 1900:
        delta = datetime.now() - lp
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        if mins > 0:
            last_ping_ago = f'{mins}m {secs}s ago'
        else:
            last_ping_ago = f'{secs}s ago'
    return jsonify({
        'id': device_id,
        'name': device['DeviceFName'],
        'is_online': is_online,
        'last_ping_ago': last_ping_ago,
        'ip': device.get('IpAddress', ''),
    })

def get_all_devices_simple():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DeviceId, DeviceFName, SerialNumber, IpAddress, DeviceLocation FROM Devices ORDER BY DeviceFName")
    cols = [desc[0] for desc in cursor.description]
    rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows

@app.route('/api/managers/add', methods=['POST'])
def api_managers_add():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    label = data.get('label', '').strip()
    location = data.get('location', '').strip()
    device_id = data.get('device_id')
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    users_path = os.path.abspath(TRACKING_USERS_FILE)
    if not os.path.exists(users_path):
        return jsonify({'error': 'Users file not found'}), 500
    with open(users_path, 'r') as f:
        users = json.load(f)
    if username in users:
        return jsonify({'error': 'Username already exists'}), 409
    entry = {'password': password, 'role': 'manager', 'label': label or username}
    if device_id:
        entry['device_id'] = int(device_id)
    if location:
        entry['location'] = location
    users[username] = entry
    with open(users_path, 'w') as f:
        json.dump(users, f, indent=2)
    return jsonify({'success': True, 'username': username})

@app.route('/api/managers', methods=['GET'])
def api_managers():
    users_path = os.path.abspath(TRACKING_USERS_FILE)
    users = {}
    if os.path.exists(users_path):
        with open(users_path, 'r') as f:
            users = json.load(f)
    managers = []
    for uname, u in users.items():
        if u.get('role') == 'manager' and u.get('device_id') in (48, None):
            managers.append({
                'username': uname,
                'label': u.get('label', uname),
                'device_id': u.get('device_id'),
                'location': u.get('location', ''),
            })
    devices = get_all_devices_simple()
    return jsonify({'managers': managers, 'devices': devices})

@app.route('/api/managers/assign', methods=['POST'])
def api_managers_assign():
    data = request.get_json()
    username = data.get('username')
    device_id = data.get('device_id')
    if not username:
        return jsonify({'error': 'Username required'}), 400
    users_path = os.path.abspath(TRACKING_USERS_FILE)
    if not os.path.exists(users_path):
        return jsonify({'error': 'Users file not found'}), 500
    with open(users_path, 'r') as f:
        users = json.load(f)
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    if device_id is not None:
        users[username]['device_id'] = int(device_id)
    else:
        users[username].pop('device_id', None)
    with open(users_path, 'w') as f:
        json.dump(users, f, indent=2)
    return jsonify({'success': True, 'username': username, 'device_id': device_id})

def check_tracking_login(username, password):
    users = TRACKING_USERS
    user_data = users.get(username)
    if user_data and user_data.get('password') == password:
        return user_data
    return None

@app.route('/track/aswins-ho', methods=['GET', 'POST'])
@app.route('/track/aswins-ho/<path:subpath>', methods=['GET', 'POST'])
def track_aswins_ho(subpath=None):
    devices = get_all_devices()
    device = None
    for d in devices:
        if d['DeviceFName'] == 'Aswins_HO':
            device = d
            break
    if not device:
        abort(404)

    error = None

    if request.args.get('logout'):
        session.pop('track_user', None)
        session.pop('track_role', None)
        session.pop('track_device_id', None)
        return redirect(url_for('track_aswins_ho'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user_data = check_tracking_login(username, password)
        if user_data:
            session['track_user'] = username
            session['track_role'] = user_data.get('role', 'manager')
            session['track_device_id'] = user_data.get('device_id', 48)
        else:
            error = 'Invalid username or password'

    logged_in = session.get('track_user') is not None
    is_admin = logged_in and session.get('track_role') in ('admin', 'superadmin')

    return render_template('track.html', device=device, logged_in=logged_in, is_admin=is_admin, error=error)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
