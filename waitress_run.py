import sys
sys.path.insert(0, r'C:\inetpub\wwwroot\device_form')
from waitress import serve
from app import app
serve(app, host='0.0.0.0', port=80)
