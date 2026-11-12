"""Exercise the real sketch with simulated GPIO/PWM, without powering motors.

Uses a local C++ compiler. This verifies command handling and duty values;
it cannot verify the vehicle's wiring or motor driver hardware.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SKETCH = PROJECT / "drivy_socket_client_customSteuerung/drivy_socket_client_customSteuerung.ino"

ARDUINO_STUB = r'''
#pragma once
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <string>
constexpr int LOW = 0, HIGH = 1, OUTPUT = 1;
int pinChannel[64];
int pinDuty[64] = {};
int pinLevel[64] = {};
inline int constrain(int value, int low, int high) {
  return std::max(low, std::min(high, value));
}
inline void pinMode(uint8_t, int) {}
inline void digitalWrite(uint8_t pin, int level) { pinLevel[pin] = level; }
inline void delay(unsigned long) {}
unsigned long testMillis = 2000;
inline unsigned long millis() { return testMillis; }
struct SerialStub {
  void begin(int) {}
  void println(const char*) {}
  template<class... Args> void printf(const char*, Args...) {}
} Serial;
inline void attachPin(uint8_t pin, uint8_t channel) {
  for (int other = 0; other < 64; ++other)
    assert(pinChannel[other] != channel);  // Each output must be independent.
  pinChannel[pin] = channel;
}
#if ESP_ARDUINO_VERSION_MAJOR >= 3
inline bool ledcAttachChannel(uint8_t pin, uint32_t, uint8_t, uint8_t channel) {
  attachPin(pin, channel);
  return true;
}
#else
inline double ledcSetup(uint8_t, uint32_t frequency, uint8_t) { return frequency; }
inline void ledcAttachPin(uint8_t pin, uint8_t channel) { attachPin(pin, channel); }
#endif
inline bool ledcWrite(uint8_t address, uint32_t duty) {
  int pin = address;
#if ESP_ARDUINO_VERSION_MAJOR < 3
  pin = -1;
  for (int candidate = 0; candidate < 64; ++candidate)
    if (pinChannel[candidate] == address) pin = candidate;
#endif
  assert(pin >= 0 && pin < 64 && pinChannel[pin] >= 0 && duty <= 255);
  pinDuty[pin] = duty;
  // Reversing direction must clear the previous input first.
  assert(!(pinDuty[19] && pinDuty[18]));
  assert(!(pinDuty[16] && pinDuty[17]));
  return true;
}
'''

WIFI_STUB = r'''
#pragma once
constexpr int WL_CONNECTED = 3;
struct WiFiStub {
  void begin(const char*, const char*) {}
  int status() { return WL_CONNECTED; }
} WiFi;
struct WiFiClient {
  bool isConnected = true;
  std::string incoming;
  bool connected() { return isConnected; }
  bool connect(const char*, uint16_t) { isConnected = true; return true; }
  void stop() { isConnected = false; }
  void setNoDelay(bool) {}
  int available() { return static_cast<int>(incoming.size()); }
  int read() {
    unsigned char value = incoming.front();
    incoming.erase(0, 1);
    return value;
  }
};
'''

HARNESS = r'''
#include "Arduino.h"
// SKETCH_INCLUDE
void expect(int rightUp, int rightDown, int leftUp, int leftDown) {
  // Vehicle test confirmed: bridge A (GPIO19/18) is LEFT, B (GPIO17/16) RIGHT.
  assert(pinDuty[17] == rightUp && pinDuty[16] == rightDown);
  assert(pinDuty[19] == leftUp && pinDuty[18] == leftDown);
}
int main() {
  std::fill_n(pinChannel, 64, -1);
  setup();
  assert(motorsReady && pinLevel[4] == HIGH);
  assert(pinChannel[17] == 0 && pinChannel[16] == 1);
  assert(pinChannel[19] == 2 && pinChannel[18] == 3);
  expect(0, 0, 0, 0);
  client.incoming = "120 0\n";
  loop();
  expect(0, 0, 0, 120);  // Left-only diagnostic.
  client.incoming = "-120 0\n";
  loop();
  expect(0, 0, 120, 0);
  client.incoming = "0 -120\n";
  loop();
  expect(0, 120, 0, 0);
  client.incoming = "50 150\n";
  loop();
  expect(150, 0, 0, 50);  // Auto-follow: person left.
  client.incoming = "100 100\n";
  loop();
  expect(100, 0, 0, 100);  // Auto-follow: person centered.
  client.incoming = "150 50\n";
  loop();
  expect(50, 0, 0, 150);  // Auto-follow: person right.
  client.incoming = "0 120\n";
  loop();
  expect(120, 0, 0, 0);  // Right only; left GPIOs must stay at zero.
  client.incoming = "120 120\n";
  loop();
  expect(120, 0, 0, 120);  // Equal speeds must drive BOTH sides.
  for (int left = -255; left <= 255; ++left) {
    drive(left, 120);
    assert(pinDuty[17] == 120 && pinDuty[16] == 0);
  }
  for (int right = -255; right <= 255; ++right) {
    drive(120, right);
    assert(pinDuty[18] == 120 && pinDuty[19] == 0);
  }
  drive(-120, -80);
  expect(0, 80, 120, 0);
  drive(0, 0);
  expect(0, 0, 0, 0);
  client.incoming = "0 1";
  loop();
  expect(0, 0, 0, 0);  // A partial TCP packet must not become a command.
  client.incoming = "20\n";
  loop();
  expect(120, 0, 0, 0);
  for (const char* invalid : {"bad\n", "1\n", "1 2 extra\n", "0 256\n",
                            "0 -999999999999999999999\n"}) {
    drive(120, 120);
    client.incoming = invalid;
    loop();
    expect(0, 0, 0, 0);
  }
  client.incoming = std::string(40, '9') + "\n120 0\r\n";
  loop();
  expect(0, 0, 0, 120);  // Recover after an overlong line.
  client.incoming = "100 100\n";
  loop();
  testMillis += COMMAND_TIMEOUT_MS - 1;
  loop();
  expect(100, 0, 0, 100);  // Moving while valid command is still fresh.
  client.incoming = "120 ";
  loop();
  testMillis += 1;
  loop();
  expect(0, 0, 0, 0);  // Incomplete packets cannot refresh the watchdog.
  client.incoming = "120\n";
  loop();
  expect(120, 0, 0, 120);  // Complete fresh command permits motion again.
  testMillis += COMMAND_TIMEOUT_MS - 1;
  client.incoming = "120 120\n";
  loop();
  testMillis += COMMAND_TIMEOUT_MS - 1;
  loop();
  expect(120, 0, 0, 120);  // Periodic GUI refresh preserves manual drive.
  client.isConnected = false;
  loop();
  expect(0, 0, 0, 0);
}
'''


@unittest.skipUnless(shutil.which("g++"), "g++ is required for the firmware simulation")
class MotorFirmwareTests(unittest.TestCase):
    def test_independent_tracks_and_commands_on_core_2_and_3(self):
        with tempfile.TemporaryDirectory(prefix="tank-motor-test-") as directory:
            folder = Path(directory)
            (folder / "Arduino.h").write_text(ARDUINO_STUB, encoding="utf-8")
            (folder / "WiFi.h").write_text(WIFI_STUB, encoding="utf-8")
            (folder / "esp_arduino_version.h").write_text("", encoding="utf-8")
            harness = HARNESS.replace("// SKETCH_INCLUDE", f'#include "{SKETCH.as_posix()}"')
            source = folder / "check.cpp"
            source.write_text(harness, encoding="utf-8")
            for core_major in (2, 3):
                with self.subTest(core_major=core_major):
                    executable = folder / f"check-core-{core_major}.exe"
                    compiled = subprocess.run([
                        shutil.which("g++"), "-std=c++17", "-O2", "-Wall", "-Wextra",
                        "-Wno-unused-parameter", f"-DESP_ARDUINO_VERSION_MAJOR={core_major}",
                        "-I", str(folder), str(source), "-o", str(executable),
                    ], capture_output=True, text=True, timeout=30)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
