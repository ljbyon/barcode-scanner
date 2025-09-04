# requirements.txt
flask==2.3.3
gunicorn==21.2.0

# app.py (your main Flask file - same as before)
from flask import Flask, render_template_string, jsonify, request
import json

app = Flask(__name__)

# Store scanned barcodes in memory (use database in production)
scanned_barcodes = []

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/barcode', methods=['POST'])
def save_barcode():
    data = request.json
    barcode_data = {
        'value': data['value'],
        'format': data['format'],
        'timestamp': data['timestamp']
    }
    scanned_barcodes.append(barcode_data)
    print(f"Scanned: {barcode_data['format']} - {barcode_data['value']}")
    return jsonify({'status': 'success'})

@app.route('/api/barcodes')
def get_barcodes():
    return jsonify(scanned_barcodes)

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Barcode Scanner",
        "short_name": "Scanner",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#000000",
        "icons": [
            {
                "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' fill='%23000'/%3E%3Ctext y='50' x='50' fill='white' text-anchor='middle' dy='0.3em'%3E📱%3C/text%3E%3C/svg%3E",
                "sizes": "192x192",
                "type": "image/svg+xml"
            }
        ]
    })

@app.route('/sw.js')
def service_worker():
    return '''
// Service Worker for offline functionality
const CACHE_NAME = 'barcode-scanner-v1';
const urlsToCache = [
    '/',
    'https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js'
];

// Install event - cache resources
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
    );
});

// Fetch event - serve from cache when offline
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => {
                return response || fetch(event.request);
            })
    );
});

let offlineBarcodes = [];

self.addEventListener('message', event => {
    if (event.data.type === 'CACHE_BARCODE') {
        offlineBarcodes.push(event.data.barcode);
    }
});
''', {'Content-Type': 'application/javascript'}

# HTML Template with JavaScript barcode scanner
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Barcode Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#000000">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js"></script>
    <style>
        body { margin: 0; padding: 20px; font-family: Arial, sans-serif; }
        #scanner-container { position: relative; width: 100%; max-width: 400px; }
        #interactive { width: 100%; height: 300px; }
        #result { margin-top: 20px; padding: 10px; background: #f0f0f0; }
        button { padding: 10px 20px; margin: 10px 0; font-size: 16px; }
        .barcode-item { padding: 5px; border-bottom: 1px solid #ccc; }
    </style>
</head>
<body>
    <h1>Mobile Barcode Scanner</h1>
    
    <button onclick="startScanner()">Start Camera</button>
    <button onclick="stopScanner()">Stop Camera</button>
    
    <div id="scanner-container">
        <div id="interactive" class="viewport"></div>
    </div>
    
    <div id="result">
        <h3>Last Scanned:</h3>
        <div id="barcode-result">No barcode scanned yet</div>
    </div>
    
    <div id="history">
        <h3>Scan History:</h3>
        <div id="barcode-list"></div>
        <button onclick="loadHistory()">Refresh History</button>
        <button onclick="syncOfflineData()" id="syncBtn" style="display:none;">Sync Offline Data</button>
        <div id="status">Status: <span id="connection-status">Checking...</span></div>
    </div>

    <script>
        let isScanning = false;
        let offlineBarcodes = JSON.parse(localStorage.getItem('offlineBarcodes') || '[]');
        
        // Register service worker for offline functionality
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js')
                .then(registration => console.log('SW registered'))
                .catch(error => console.log('SW registration failed'));
        }
        
        function startScanner() {
            if (isScanning) return;
            
            Quagga.init({
                inputStream: {
                    name: "Live",
                    type: "LiveStream",
                    target: document.querySelector('#interactive'),
                    constraints: {
                        width: 400,
                        height: 300,
                        facingMode: "environment"
                    }
                },
                decoder: {
                    readers: [
                        "code_128_reader",
                        "ean_reader",
                        "ean_8_reader",
                        "code_39_reader",
                        "code_39_vin_reader",
                        "codabar_reader",
                        "upc_reader",
                        "upc_e_reader"
                    ]
                }
            }, function(err) {
                if (err) {
                    console.log("Error starting scanner:", err);
                    alert("Error starting camera: " + err);
                    return;
                }
                console.log("Initialization finished. Ready to start");
                Quagga.start();
                isScanning = true;
            });
        }
        
        function stopScanner() {
            if (isScanning) {
                Quagga.stop();
                isScanning = false;
            }
        }
        
        // Handle barcode detection
        Quagga.onDetected(function(result) {
            const code = result.codeResult.code;
            const format = result.codeResult.format;
            const barcode = {
                value: code,
                format: format,
                timestamp: new Date().toISOString()
            };
            
            document.getElementById('barcode-result').innerHTML = 
                `<strong>${format}:</strong> ${code}`;
            
            // Store locally first (for offline use)
            offlineBarcodes.push(barcode);
            localStorage.setItem('offlineBarcodes', JSON.stringify(offlineBarcodes));
            
            // Try to send to server (if online)
            fetch('/api/barcode', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(barcode)
            })
            .then(response => response.json())
            .then(data => {
                console.log('Barcode saved to server:', data);
                loadHistory();
            })
            .catch(error => {
                console.log('Offline mode - stored locally:', error);
                loadHistory();
            });
        });
        
        function loadHistory() {
            // Try to load from server first, fallback to local storage
            fetch('/api/barcodes')
            .then(response => response.json())
            .then(serverBarcodes => {
                displayHistory(serverBarcodes, 'Server');
            })
            .catch(error => {
                // Offline - show local storage data
                displayHistory(offlineBarcodes, 'Local (Offline)');
            });
        }
        
        function displayHistory(barcodes, source) {
            const list = document.getElementById('barcode-list');
            list.innerHTML = `<p><em>Source: ${source}</em></p>`;
            
            barcodes.slice(-10).reverse().forEach(barcode => {
                const div = document.createElement('div');
                div.className = 'barcode-item';
                div.innerHTML = `<strong>${barcode.format}:</strong> ${barcode.value}`;
                list.appendChild(div);
            });
        }
        
        function syncOfflineData() {
            if (offlineBarcodes.length === 0) {
                alert('No offline data to sync');
                return;
            }
            
            Promise.all(offlineBarcodes.map(barcode => 
                fetch('/api/barcode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(barcode)
                })
            ))
            .then(() => {
                alert(`Synced ${offlineBarcodes.length} barcodes to server`);
                offlineBarcodes = [];
                localStorage.removeItem('offlineBarcodes');
                document.getElementById('syncBtn').style.display = 'none';
                loadHistory();
            })
            .catch(error => {
                alert('Sync failed - still offline');
            });
        }
        
        function checkConnection() {
            fetch('/api/barcodes')
            .then(() => {
                document.getElementById('connection-status').textContent = 'Online';
                document.getElementById('connection-status').style.color = 'green';
                if (offlineBarcodes.length > 0) {
                    document.getElementById('syncBtn').style.display = 'inline-block';
                }
            })
            .catch(() => {
                document.getElementById('connection-status').textContent = 'Offline';
                document.getElementById('connection-status').style.color = 'red';
                document.getElementById('syncBtn').style.display = 'none';
            });
        }
        
        // Check connection status periodically
        setInterval(checkConnection, 5000);
        checkConnection();
        
        // Load history on page load
        loadHistory();
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
