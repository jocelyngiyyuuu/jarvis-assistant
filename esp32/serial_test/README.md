# ESP32-S3 N16R8 serial test

Firmware này kiểm tra ESP32-S3 với 16 MB flash và 8 MB OPI PSRAM mà không
điều khiển GPIO.

## Build

```bash
.pio-venv/bin/pio run --project-dir esp32/serial_test
```

## Upload

Đưa board vào bootloader nếu upload không tự kết nối: giữ `BOOT`, nhấn-thả
`RST/EN`, sau đó thả `BOOT`.

```bash
.pio-venv/bin/pio run --project-dir esp32/serial_test --target upload
```

## Test Serial

```bash
.pio-venv/bin/pio device monitor --project-dir esp32/serial_test
```

Kết quả đúng sẽ chứa `Flash: 16 MB`, `PSRAM: 8 MB` và `RESULT: PASS`.
