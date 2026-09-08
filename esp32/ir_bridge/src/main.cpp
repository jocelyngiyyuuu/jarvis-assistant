#include <Arduino.h>
#include <IRrecv.h>
#include <IRremoteESP8266.h>
#include <IRsend.h>
#include <IRutils.h>
#include <driver/i2s.h>
#include <math.h>
#include <string.h>

namespace {
constexpr uint16_t kReceivePin = 7;
constexpr uint16_t kTransmitPin = 8;
constexpr uint16_t kCaptureBufferSize = 1024;
constexpr uint8_t kCaptureTimeoutMs = 50;
constexpr i2s_port_t kI2sPort = I2S_NUM_0;
constexpr gpio_num_t kMicSckPin = GPIO_NUM_4;
constexpr gpio_num_t kMicWsPin = GPIO_NUM_5;
constexpr gpio_num_t kMicSdPin = GPIO_NUM_6;
constexpr size_t kAudioFrames = 256;
constexpr uint32_t kAudioFrameMs = 16;
constexpr uint64_t kFanPowerSpeedCode = 0x01FE807F;
constexpr uint64_t kFanSwingCode = 0x01FE40BF;
constexpr uint64_t kFanOffCode = 0x01FEA05F;
constexpr uint16_t kFanCommandGapMs = 400;
constexpr uint8_t kFanOffRepeats = 3;
// A physical button press from the fan remote sends one full NEC frame and
// one NEC repeat frame about 40 ms later.  Reproduce both frames so the fan
// sees the same waveform at normal room distance.
constexpr uint16_t kFanNecRepeatFrames = 1;
constexpr bool kClapControlEnabled = false;
constexpr bool kUsbAudioStreaming = true;
constexpr uint8_t kAudioMagic0 = 0xA5;
constexpr uint8_t kAudioMagic1 = 0x5A;
// The INMP441's native level is too quiet for far-field speech after its
// 24-bit samples are converted to PCM16.  Four-times digital gain keeps the
// measured far voice well inside PCM16 while saturation protects close speech.
constexpr int32_t kUsbAudioGain = 4;

constexpr uint8_t kCommandA[] = {
    0xC3, 0x90, 0x00, 0x00, 0xA0, 0x40, 0x20,
    0x00, 0x80, 0x00, 0x00, 0x45, 0x18};
constexpr uint8_t kCommandB[] = {
    0xC3, 0x90, 0x00, 0x00, 0xA0, 0x40, 0x20,
    0x00, 0x80, 0x20, 0x00, 0x45, 0x38};

IRrecv receiver(kReceivePin, kCaptureBufferSize, kCaptureTimeoutMs, true);
IRsend transmitter(kTransmitPin);
decode_results results;
uint32_t lastStatusMs = 0;
int32_t audioSamples[kAudioFrames];
int16_t usbAudioSamples[kAudioFrames];
double noiseRms = 15000.0;
uint16_t calibrationFrames = 0;
bool impulseActive = false;
uint16_t impulseFrames = 0;
uint16_t quietFrames = 0;
double impulseMaxRms = 0;
int32_t impulsePeak = 0;
double impulseMaxZcr = 0;
double impulseMaxHighFreqRatio = 0;
uint32_t firstImpulseMs = 0;
bool firstImpulseStrong = false;
uint32_t cooldownUntilMs = 0;
char usbCommand[32] = {};
size_t usbCommandLength = 0;
bool fanAssumedOn = false;
bool fanSwingAssumedOn = false;

void sendCommand(const uint8_t command[], const char *name) {
  receiver.disableIRIn();
  transmitter.sendElectraAC(command, sizeof(kCommandA));
  delay(100);
  receiver.enableIRIn();
  Serial.printf("SENT %s via GPIO%u\n", name, kTransmitPin);
}

void setFanToSpeed3() {
  receiver.disableIRIn();
  for (uint8_t press = 0; press < 3; press++) {
    transmitter.sendNEC(
        kFanPowerSpeedCode, kNECBits, kFanNecRepeatFrames);
    if (press < 2) delay(kFanCommandGapMs);
  }
  delay(100);
  receiver.enableIRIn();
  fanAssumedOn = true;
  Serial.println("FAN SPEED 3: SENT NEC 0x01FE807F x3");
}

void toggleFanSwing() {
  receiver.disableIRIn();
  transmitter.sendNEC(kFanSwingCode, kNECBits, kFanNecRepeatFrames);
  delay(100);
  receiver.enableIRIn();
  fanSwingAssumedOn = !fanSwingAssumedOn;
  Serial.println("FAN SWING: SENT NEC 0x01FE40BF");
}

void sendFanOff() {
  receiver.disableIRIn();
  for (uint8_t press = 0; press < kFanOffRepeats; press++) {
    transmitter.sendNEC(kFanOffCode, kNECBits, kFanNecRepeatFrames);
    if (press + 1 < kFanOffRepeats) delay(kFanCommandGapMs);
  }
  delay(100);
  receiver.enableIRIn();
  fanAssumedOn = false;
  fanSwingAssumedOn = false;
  Serial.println("FAN OFF: SENT NEC 0x01FEA05F x3");
}

void ensureFanOff(const char *source) {
  // Always transmit OFF. The physical fan may have been changed with its
  // remote or ESP32 may have rebooted, so RAM state is not authoritative.
  sendFanOff();
  Serial.printf("FAN STATE: OFF (%s)\n", source);
}

void ensureFanOn() {
  // Always transmit.  The physical fan may have missed a previous IR frame
  // or been changed by its handheld remote, so RAM state must not suppress a
  // deliberate voice command.
  setFanToSpeed3();
  delay(kFanCommandGapMs);
  if (!fanSwingAssumedOn) toggleFanSwing();
  Serial.println("FAN STATE: ON SPEED 3 + SWING ON");
}

void toggleFanFromDoubleClap() {
  if (!fanAssumedOn) {
    ensureFanOn();
    return;
  }

  ensureFanOff("DOUBLE_CLAP");
}

void handleUsbCommand(const char *command) {
  if (strcmp(command, "CMD AC_A") == 0) {
    sendCommand(kCommandA, "A");
  } else if (strcmp(command, "CMD AC_B") == 0) {
    sendCommand(kCommandB, "B");
  } else if (strcmp(command, "CMD FAN3") == 0) {
    setFanToSpeed3();
  } else if (strcmp(command, "CMD SWING") == 0) {
    toggleFanSwing();
  } else if (strcmp(command, "CMD OFF") == 0) {
    ensureFanOff("USB");
  } else if (strcmp(command, "CMD DEVICE_ON") == 0) {
    ensureFanOn();
  } else if (strcmp(command, "CMD DEVICE_OFF") == 0) {
    ensureFanOff("VOICE");
  } else if (strcmp(command, "HELP") == 0) {
    Serial.println(
        "CMD AC_A | CMD AC_B | CMD FAN3 | CMD SWING | CMD OFF | "
        "CMD DEVICE_ON | CMD DEVICE_OFF");
  } else {
    Serial.printf("IGNORED USB COMMAND: %s\n", command);
  }
}

void processUsbCommands() {
  while (Serial.available()) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\n' || value == '\r') {
      if (usbCommandLength > 0) {
        usbCommand[usbCommandLength] = '\0';
        handleUsbCommand(usbCommand);
        usbCommandLength = 0;
      }
    } else if (value >= 32 && value <= 126 &&
               usbCommandLength < sizeof(usbCommand) - 1) {
      usbCommand[usbCommandLength++] = value;
    } else if (usbCommandLength >= sizeof(usbCommand) - 1) {
      usbCommandLength = 0;
    }
  }
}

bool setupMicrophone() {
  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = 16000,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };
  const i2s_pin_config_t pins = {
      .bck_io_num = kMicSckPin,
      .ws_io_num = kMicWsPin,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = kMicSdPin,
  };
  esp_err_t status = i2s_driver_install(kI2sPort, &config, 0, nullptr);
  if (status == ESP_OK) status = i2s_set_pin(kI2sPort, &pins);
  return status == ESP_OK;
}

void finishImpulse(uint32_t now, double onsetPeak) {
  const uint32_t durationMs = impulseFrames * kAudioFrameMs;
  const double crest = impulsePeak / max(1.0, impulseMaxRms);
  const bool strictClapShape =
      durationMs <= 160 && impulseMaxZcr >= 0.09 &&
      impulseMaxHighFreqRatio >= 0.12;
  const bool clearClapWithTail =
      durationMs <= 192 && impulseMaxZcr >= 0.40 &&
      impulseMaxHighFreqRatio >= 0.40;
  // A strong second clap can ring for one or two extra frames. Allow that
  // only while a first clap is pending, and require a much clearer high-
  // frequency signature than the normal short-clap path.
  const bool secondClapWithTail =
      firstImpulseMs != 0 && durationMs <= 192 && impulseMaxZcr >= 0.30 &&
      impulseMaxHighFreqRatio >= 0.24;
  // Far claps are quieter, so admit them only with a much stricter spectral
  // signature. Two such impulses are required; a weak single never turns a
  // device off.
  const bool remoteClapShape =
      durationMs <= 192 && impulseMaxZcr >= 0.45 &&
      impulseMaxHighFreqRatio >= 0.42;
  const bool strongSharp = impulsePeak >= 1300000 && crest >= 2.4 &&
                           (strictClapShape || clearClapWithTail ||
                            secondClapWithTail);
  const bool remoteSharp = impulsePeak >= onsetPeak && crest >= 2.8 &&
                           remoteClapShape;
  const bool sharp = strongSharp || remoteSharp;
  Serial.printf(
      "CLAP CANDIDATE accepted=%d strong=%d duration=%lums crest=%.2f "
      "zcr=%.3f hf=%.3f rms=%.0f peak=%ld\n",
      sharp, strongSharp, static_cast<unsigned long>(durationMs), crest,
      impulseMaxZcr, impulseMaxHighFreqRatio, impulseMaxRms,
      static_cast<long>(impulsePeak));
  impulseActive = false;
  impulseFrames = 0;
  quietFrames = 0;

  if (!sharp) return;
  if (firstImpulseMs != 0) {
    const uint32_t interval = now - firstImpulseMs;
    if (interval >= 120 && interval <= 850) {
      firstImpulseMs = 0;
      firstImpulseStrong = false;
      cooldownUntilMs = now + 1200;
      Serial.println("EVENT DOUBLE_CLAP");
      toggleFanFromDoubleClap();
      return;
    }
  }
  firstImpulseMs = now;
  firstImpulseStrong = strongSharp;
}

void processMicrophone() {
  size_t bytesRead = 0;
  if (i2s_read(kI2sPort, audioSamples, sizeof(audioSamples), &bytesRead,
               pdMS_TO_TICKS(100)) != ESP_OK || bytesRead == 0) {
    return;
  }

  const size_t sampleCount = bytesRead / sizeof(audioSamples[0]);
  const size_t channelCount = sampleCount;
  double sum = 0;
  int32_t peak = 0;
  for (size_t index = 0; index < sampleCount; index++) {
    const int32_t value = audioSamples[index] >> 8;
    sum += value;
    peak = max(peak, abs(value));
  }
  const double mean = channelCount ? sum / channelCount : 0;
  double squares = 0;
  double differenceSquares = 0;
  double previous = 0;
  size_t zeroCrossings = 0;
  bool hasPrevious = false;
  for (size_t index = 0; index < sampleCount; index++) {
    const double value = (audioSamples[index] >> 8) - mean;
    squares += value * value;
    if (hasPrevious) {
      const double difference = value - previous;
      differenceSquares += difference * difference;
      if ((previous < 0 && value >= 0) || (previous >= 0 && value < 0)) {
        zeroCrossings++;
      }
    }
    previous = value;
    hasPrevious = true;
  }
  const double rms = channelCount ? sqrt(squares / channelCount) : 0;
  const double zcr = channelCount > 1
                         ? static_cast<double>(zeroCrossings) / (channelCount - 1)
                         : 0;
  const double highFreqRatio = differenceSquares / max(1.0, squares * 4.0);

  if (calibrationFrames < 100) {
    noiseRms = noiseRms * 0.95 + rms * 0.05;
    calibrationFrames++;
    return;
  }

  const uint32_t now = millis();
  const double onsetRms = max(45000.0, noiseRms * 5.5);
  const double onsetPeak = max(500000.0, noiseRms * 18.0);
  const bool loud = rms >= onsetRms && peak >= onsetPeak;

  if (firstImpulseMs != 0 && !impulseActive && now - firstImpulseMs > 850) {
    const bool shouldTurnOff = firstImpulseStrong;
    firstImpulseMs = 0;
    firstImpulseStrong = false;
    cooldownUntilMs = now + 1200;
    if (shouldTurnOff) {
      Serial.println("EVENT SINGLE_CLAP");
      ensureFanOff("SINGLE_CLAP");
    } else {
      Serial.println("CLAP REMOTE SINGLE IGNORED");
    }
  }
  if (!impulseActive) {
    if (now >= cooldownUntilMs && loud) {
      impulseActive = true;
      impulseFrames = 1;
      quietFrames = 0;
      impulseMaxRms = rms;
      impulsePeak = peak;
      impulseMaxZcr = zcr;
      impulseMaxHighFreqRatio = highFreqRatio;
    } else if (!loud) {
      const double bounded = min(rms, noiseRms * 2.0);
      const double weight = bounded < noiseRms ? 0.02 : 0.005;
      noiseRms = noiseRms * (1.0 - weight) + bounded * weight;
    }
    return;
  }

  impulseFrames++;
  impulseMaxRms = max(impulseMaxRms, rms);
  impulsePeak = max(impulsePeak, peak);
  impulseMaxZcr = max(impulseMaxZcr, zcr);
  impulseMaxHighFreqRatio = max(impulseMaxHighFreqRatio, highFreqRatio);
  quietFrames = rms < onsetRms * 0.45 ? quietFrames + 1 : 0;
  if (quietFrames >= 2 || impulseFrames * kAudioFrameMs >= 260) {
    finishImpulse(now, onsetPeak);
  }
}

void streamMicrophoneAudio() {
  size_t bytesRead = 0;
  if (i2s_read(kI2sPort, audioSamples, sizeof(audioSamples), &bytesRead,
               pdMS_TO_TICKS(100)) != ESP_OK || bytesRead == 0) {
    return;
  }
  const size_t sampleCount = bytesRead / sizeof(audioSamples[0]);
  const size_t channelCount = min(sampleCount, kAudioFrames);
  for (size_t channel = 0; channel < channelCount; channel++) {
    const int32_t scaled =
        (audioSamples[channel] >> 15) * kUsbAudioGain;
    usbAudioSamples[channel] = static_cast<int16_t>(
        constrain(scaled, static_cast<int32_t>(INT16_MIN),
                  static_cast<int32_t>(INT16_MAX)));
  }
  const uint16_t payloadBytes = channelCount * sizeof(usbAudioSamples[0]);
  const uint8_t header[] = {
      kAudioMagic0,
      kAudioMagic1,
      static_cast<uint8_t>(payloadBytes & 0xFF),
      static_cast<uint8_t>(payloadBytes >> 8),
  };
  Serial.write(header, sizeof(header));
  Serial.write(reinterpret_cast<const uint8_t *>(usbAudioSamples), payloadBytes);
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);

  transmitter.begin();
  receiver.enableIRIn();
  const bool microphoneReady = setupMicrophone();

  Serial.println();
  Serial.println("=== JARVIS ESP32 BRIDGE ===");
  Serial.printf("INMP441 USB audio: %s | SCK=%d WS=%d SD=%d\n",
                microphoneReady ? "OK" : "FAILED",
                kMicSckPin, kMicWsPin, kMicSdPin);
  Serial.printf("IR Receiver: GPIO%u | IR Transmitter: GPIO%u\n", kReceivePin,
                kTransmitPin);
  Serial.println("Lenh USB: A = ma thu nhat, B = ma thu hai, H = tro giup.");
  Serial.println("AUDIO PCM16 16000Hz framed with A5 5A + uint16 length.");
}

void loop() {
  processUsbCommands();

  if (kUsbAudioStreaming) {
    streamMicrophoneAudio();
  } else if (kClapControlEnabled) {
    processMicrophone();
  }

  if (!kUsbAudioStreaming && receiver.decode(&results)) {
    Serial.println("--- IR SIGNAL RECEIVED ---");
    Serial.println(resultToHumanReadableBasic(&results));
    Serial.println("--- END IR SIGNAL ---");
    receiver.resume();
  }

  const uint32_t now = millis();
  if (kClapControlEnabled && now - lastStatusMs >= 5000) {
    lastStatusMs = now;
    Serial.printf("STATUS MIC noise=%.0f threshold=%.0f\n", noiseRms,
                  max(90000.0, noiseRms * 5.5));
  }
}
