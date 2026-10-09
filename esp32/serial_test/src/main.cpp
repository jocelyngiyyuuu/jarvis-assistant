#include <Arduino.h>
#include <esp_chip_info.h>
#include <esp_flash.h>
#include <esp_heap_caps.h>

namespace {
constexpr uint32_t kBaudRate = 115200;
constexpr uint32_t kReportIntervalMs = 5000;
uint32_t lastReportMs = 0;

void printBoardInfo() {
  esp_chip_info_t chipInfo{};
  esp_chip_info(&chipInfo);

  uint32_t flashSize = 0;
  const esp_err_t flashResult = esp_flash_get_size(nullptr, &flashSize);
  const uint32_t psramSize = ESP.getPsramSize();

  Serial.println();
  Serial.println("=== JARVIS ESP32-S3 N16R8 TEST ===");
  Serial.printf("Chip: ESP32-S3, cores: %d, revision: %d\n",
                chipInfo.cores, chipInfo.revision);
  Serial.printf("Flash: %lu MB%s\n",
                static_cast<unsigned long>(flashSize / (1024UL * 1024UL)),
                flashResult == ESP_OK ? "" : " (read error)");
  Serial.printf("PSRAM: %.2f MB (%lu bytes)\n",
                static_cast<double>(psramSize) / (1024.0 * 1024.0),
                static_cast<unsigned long>(psramSize));
  Serial.printf("Free heap: %lu bytes\n",
                static_cast<unsigned long>(ESP.getFreeHeap()));
  Serial.printf("Free PSRAM: %lu bytes\n",
                static_cast<unsigned long>(ESP.getFreePsram()));

  const bool configurationOk =
      flashResult == ESP_OK && flashSize == 16UL * 1024UL * 1024UL &&
      psramSize >= 8UL * 1024UL * 1024UL - 64UL * 1024UL;
  Serial.printf("RESULT: %s\n", configurationOk ? "PASS" : "CHECK CONFIGURATION");
}
}  // namespace

void setup() {
  Serial.begin(kBaudRate);
  delay(1500);
  printBoardInfo();
}

void loop() {
  const uint32_t now = millis();
  if (now - lastReportMs >= kReportIntervalMs) {
    lastReportMs = now;
    printBoardInfo();
  }
  delay(10);
}
