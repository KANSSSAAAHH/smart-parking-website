# SmartPark

Frontend HTML, CSS, dan JavaScript dengan backend REST API Python (standard library) dan database SQLite. Jalankan backend untuk membuka dashboard; database dibuat otomatis pada penggunaan pertama dan menyimpan sesi parkir serta telemetri.

## Menjalankan

Gunakan Python 3.9 atau lebih baru:

```powershell
cd smart-parking-website
$env:DEVICE_API_TOKEN = "ganti-dengan-token-perangkat"
python server.py
```

Buka `http://127.0.0.1:8080`. Tanpa telemetry, status slot akan ditampilkan sebagai **belum ada data**, bukan dianggap kosong. Mode DEMO di dashboard hanya pratinjau lokal; tidak dikirim ke backend atau ditambahkan ke riwayat.

Pengaturan opsional:

| Environment variable | Default | Fungsi |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Alamat bind web/API; sesuaikan untuk jaringan perangkat |
| `PORT` | `8080` | Port HTTP |
| `SMARTPARK_DB` | `smartpark.sqlite3` | Path file SQLite, relatif terhadap folder aplikasi jika bukan absolute |
| `DEVICE_API_TOKEN` | kosong | Token wajib untuk menerima telemetry ESP32 |
| `DEVICE_TIMEOUT_SECONDS` | `60` | Batas waktu heartbeat sebelum ESP32 ditandai OFFLINE |
| `AI_API_URL` | kosong | URL endpoint model yang kompatibel dengan format Chat Completions |
| `AI_API_KEY` | kosong | Secret model AI; hanya dibaca backend |
| `AI_MODEL` | kosong | Nama model pada provider yang dipilih |

Jika ketiga pengaturan AI kosong, endpoint assistant tetap dapat memberi jawaban faktual berbasis status/riwayat database. Jika hanya sebagian pengaturan AI diisi, backend mengembalikan error konfigurasi. Kegagalan provider juga dilaporkan, bukan diganti jawaban yang seolah-olah dari model.

## REST API

- `GET /api/health` — health check.
- `GET /api/status` — status tiga slot, jarak sensor, heartbeat ESP32, LDR, IR, dan ringkasan.
- `GET /api/history?slot=1&from=2026-01-01&to=2026-01-31&q=2` — sesi parkir persisten; filter slot/tanggal/pencarian opsional.
- `POST /api/telemetry` — menerima snapshot ESP32; token dikirim melalui `Authorization: Bearer <DEVICE_API_TOKEN>` atau `X-Device-Token`.
- `POST /api/assistant` — menerima `{ "question": "Apakah masih ada tempat parkir?" }`; model AI opsional berjalan di backend.

Contoh body telemetry:

```json
{
  "device_id": "smartpark-controller",
  "slots": [
    { "slot": 1, "status": "occupied", "distance_cm": 8.4 },
    { "slot": 2, "status": "empty", "distance_cm": 42.1 },
    { "slot": 3, "status": "empty", "distance_cm": 39.7 }
  ],
  "ldr_lux": 120.5,
  "ir_entry": false,
  "ir_exit": true
}
```

Kirim `status: "occupied"` atau `"empty"` setelah logika sensor ESP32 menentukan status; backend tidak menetapkan ambang jarak HC-SR04. Jarak, LDR, dan nilai IR opsional. `ir_entry` dan `ir_exit` adalah boolean pembacaan deteksi IR pada jalur masuk/keluar; dashboard menampilkan nilai terakhir dan waktu telemetry terkait, bukan mengklaim hitungan kendaraan. Jika field IR tidak dikirim, nilai serta timestamp pembacaan terakhir tetap tersimpan. Jika status slot tidak tersedia, jangan mengirim nilai status—slot tetap pada status terakhir dan assistant tidak menyimpulkan status baru. Timestamp penerimaan dibuat backend dalam UTC. Status ESP32 berubah menjadi OFFLINE setelah tidak ada telemetry selama batas heartbeat.

Contoh uji lokal (PowerShell):

```powershell
$headers = @{ "X-Device-Token" = $env:DEVICE_API_TOKEN }
$body = @{
  device_id = "smartpark-controller"
  slots = @(
    @{ slot = 1; status = "occupied"; distance_cm = 8.4 },
    @{ slot = 2; status = "empty"; distance_cm = 42.1 },
    @{ slot = 3; status = "empty"; distance_cm = 39.7 }
  )
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/telemetry -Headers $headers -ContentType "application/json" -Body $body
```

## Integrasi hardware

Atur Wi-Fi, alamat server, token, GPIO, ambang jarak, dan logika sensor pada firmware ESP32 saat spesifikasi hardware tersedia. Project ini sengaja tidak menetapkan pin GPIO, alamat jaringan, maupun ambang HC-SR04. Sensor IR digunakan untuk mendeteksi kendaraan di jalur masuk dan keluar. Breadboard dicatat sebagai media perakitan sementara antara ESP32, sensor, dan komponen lain—bukan sebagai sensor. Servo palang, LED putih/merah/kuning/hijau, dan OLED tetap tercatat sebagai komponen rencana; status aktuator belum ditampilkan sebagai online tanpa telemetry pendukung.
