#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>

namespace {
constexpr i2s_port_t kI2sPort = I2S_NUM_0;
constexpr gpio_num_t kSckPin = GPIO_NUM_4;
constexpr gpio_num_t kWsPin = GPIO_NUM_5;
constexpr gpio_num_t kSdPin = GPIO_NUM_6;
constexpr size_t kFrames = 256;
int32_t samples[kFrames * 2];

struct Level {
  double rms;
  int32_t peak;
};

Level measureChannel(size_t offset, size_t sampleCount) {
  double sum = 0;
  int32_t peak = 0;
  for (size_t i = offset; i < sampleCount; i += 2) {
    const int32_t value = samples[i] >> 8;
    sum += value;
    peak = max(peak, abs(value));
  }

  const size_t count = sampleCount / 2;
  const double mean = count ? sum / count : 0;
  double squares = 0;
  for (size_t i = offset; i < sampleCount; i += 2) {
    const double value = (samples[i] >> 8) - mean;
    squares += value * value;
  }
  return {count ? sqrt(squares / count) : 0, peak};
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);

  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = 16000,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };
  const i2s_pin_config_t pins = {
      .bck_io_num = kSckPin,
      .ws_io_num = kWsPin,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = kSdPin,
  };

  esp_err_t status = i2s_driver_install(kI2sPort, &config, 0, nullptr);
  if (status == ESP_OK) status = i2s_set_pin(kI2sPort, &pins);

  Serial.println("\n=== INMP441 MIC TEST ===");
  Serial.printf("SCK=GPIO%d WS=GPIO%d SD=GPIO%d\n", kSckPin, kWsPin, kSdPin);
  Serial.printf("I2S init: %s (%d)\n", status == ESP_OK ? "OK" : "FAILED",
                status);
  Serial.println("Hay noi hoac vo tay gan micro.");
}

void loop() {
  size_t bytesRead = 0;
  const esp_err_t status = i2s_read(kI2sPort, samples, sizeof(samples),
                                    &bytesRead, pdMS_TO_TICKS(1000));
  if (status != ESP_OK || bytesRead == 0) {
    Serial.printf("I2S read FAILED: %d, bytes=%u\n", status,
                  static_cast<unsigned>(bytesRead));
    delay(500);
    return;
  }

  const size_t sampleCount = bytesRead / sizeof(samples[0]);
  const Level left = measureChannel(0, sampleCount);
  const Level right = measureChannel(1, sampleCount);
  Serial.printf("MIC L rms=%8.0f peak=%7ld | R rms=%8.0f peak=%7ld\n",
                left.rms, static_cast<long>(left.peak), right.rms,
                static_cast<long>(right.peak));
  delay(150);
}
