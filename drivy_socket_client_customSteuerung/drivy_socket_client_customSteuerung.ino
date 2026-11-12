#include <Arduino.h>
#include <WiFi.h>
#include <esp_arduino_version.h>
#include <ctype.h>
#include <stdlib.h>

// DOIT ESP32 DEVKIT V1. Each motor input has its own PWM channel.
// Vehicle test confirmed: bridge A drives LEFT, bridge B drives RIGHT.
constexpr uint8_t PIN_MOTOR_RIGHT_UP = 17;  // MP6508 BIN1
constexpr uint8_t PIN_MOTOR_RIGHT_DN = 16;  // MP6508 BIN2
constexpr uint8_t PIN_MOTOR_LEFT_UP = 19;   // MP6508 AIN1
constexpr uint8_t PIN_MOTOR_LEFT_DN = 18;   // MP6508 AIN2
constexpr uint8_t PIN_SLEEP = 4;
constexpr uint8_t LED_PINS[] = {23, 15, 13, 32};

constexpr uint8_t CH_RIGHT_UP = 0;
constexpr uint8_t CH_RIGHT_DN = 1;
constexpr uint8_t CH_LEFT_UP = 2;
constexpr uint8_t CH_LEFT_DN = 3;
constexpr uint32_t PWM_FREQUENCY = 1000;
constexpr uint8_t PWM_RESOLUTION = 8;  // Duty cycle: 0..255

// Motor polarity calibrated from vehicle tests for each side.
constexpr bool INVERT_LEFT = true;
constexpr bool INVERT_RIGHT = false;

const char* ssid = "kisy1";
const char* password = "";
const char* host = "192.168.1.111";
constexpr uint16_t port = 5001;
WiFiClient client;

bool motorsReady = false;
char commandBuffer[32];
size_t commandLength = 0;
bool discardCommand = false;
unsigned long lastConnectAttempt = 0;
constexpr unsigned long COMMAND_TIMEOUT_MS = 1200;
unsigned long lastCommandAt = 0;
bool commandActive = false;

bool attachMotorPwm(uint8_t pin, uint8_t channel) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttachChannel(pin, PWM_FREQUENCY, PWM_RESOLUTION, channel);
#else
  if (ledcSetup(channel, PWM_FREQUENCY, PWM_RESOLUTION) == 0) return false;
  ledcAttachPin(pin, channel);
  return true;
#endif
}

void writeMotorPwm(uint8_t pin, uint8_t channel, uint8_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // Core 3.x addresses ledcWrite by GPIO, not by channel!
  ledcWrite(pin, duty);
#else
  ledcWrite(channel, duty);
#endif
}

void stopMotors() {
  if (!motorsReady) return;
  writeMotorPwm(PIN_MOTOR_RIGHT_UP, CH_RIGHT_UP, 0);
  writeMotorPwm(PIN_MOTOR_RIGHT_DN, CH_RIGHT_DN, 0);
  writeMotorPwm(PIN_MOTOR_LEFT_UP, CH_LEFT_UP, 0);
  writeMotorPwm(PIN_MOTOR_LEFT_DN, CH_LEFT_DN, 0);
}

void setTrack(uint8_t upPin, uint8_t downPin, uint8_t upChannel,
              uint8_t downChannel, int velocity, bool invert) {
  velocity = constrain(velocity, -255, 255);
  if (invert) velocity = -velocity;
  // Clear the opposite input before energizing a direction.
  if (velocity > 0) {
    writeMotorPwm(downPin, downChannel, 0);
    writeMotorPwm(upPin, upChannel, velocity);
  } else {
    writeMotorPwm(upPin, upChannel, 0);
    writeMotorPwm(downPin, downChannel, -velocity);
  }
}

void drive(int left, int right) {
  if (!motorsReady) return;
  // Two independent track velocities; no addition/subtraction or mixing.
  setTrack(PIN_MOTOR_LEFT_UP, PIN_MOTOR_LEFT_DN, CH_LEFT_UP, CH_LEFT_DN,
           left, INVERT_LEFT);
  setTrack(PIN_MOTOR_RIGHT_UP, PIN_MOTOR_RIGHT_DN, CH_RIGHT_UP, CH_RIGHT_DN,
           right, INVERT_RIGHT);
  Serial.printf("Drive LEFT=%d RIGHT=%d\n", left, right);
}

bool parseCommand(const char* text, int& left, int& right) {
  char* end;
  long parsedLeft = strtol(text, &end, 10);
  if (end == text || (*end != ' ' && *end != '\t')) return false;
  const char* second = end;
  long parsedRight = strtol(second, &end, 10);
  if (end == second) return false;
  while (isspace(static_cast<unsigned char>(*end))) ++end;
  if (*end != '\0' || parsedLeft < -255 || parsedLeft > 255 ||
      parsedRight < -255 || parsedRight > 255) return false;
  left = static_cast<int>(parsedLeft);
  right = static_cast<int>(parsedRight);
  return true;
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_SLEEP, OUTPUT);
  digitalWrite(PIN_SLEEP, LOW);
  for (uint8_t pin : LED_PINS) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);  // Lights that are off do not need PWM channels.
  }

  bool rightUp = attachMotorPwm(PIN_MOTOR_RIGHT_UP, CH_RIGHT_UP);
  bool rightDown = attachMotorPwm(PIN_MOTOR_RIGHT_DN, CH_RIGHT_DN);
  bool leftUp = attachMotorPwm(PIN_MOTOR_LEFT_UP, CH_LEFT_UP);
  bool leftDown = attachMotorPwm(PIN_MOTOR_LEFT_DN, CH_LEFT_DN);
  motorsReady = rightUp && rightDown && leftUp && leftDown;
  if (!motorsReady) {
    Serial.println("PWM initialization failed. Motor driver stays disabled.");
    return;
  }
  stopMotors();
  digitalWrite(PIN_SLEEP, HIGH);
  Serial.println("PWM: LEFT/A GPIO19=CH2 GPIO18=CH3; RIGHT/B GPIO17=CH0 GPIO16=CH1");

  WiFi.begin(ssid, password);
  Serial.println("Connecting to WiFi...");
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("WiFi connected. Waiting for GUI server...");
}

void loop() {
  if (!motorsReady) {
    delay(100);
    return;
  }
  if (WiFi.status() != WL_CONNECTED || !client.connected()) {
    stopMotors();
    commandActive = false;
    commandLength = 0;
    discardCommand = false;
    if (WiFi.status() == WL_CONNECTED && millis() - lastConnectAttempt >= 2000) {
      lastConnectAttempt = millis();
      client.stop();
      Serial.printf("Connecting to %s:%u...\n", host, port);
      if (client.connect(host, port)) {
        client.setNoDelay(true);
        Serial.println("Connected to GUI. Protocol: LEFT RIGHT followed by newline.");
      }
    }
    delay(10);
    return;
  }

  // Process complete lines only; TCP may split a command across packets.
  while (client.available()) {
    char ch = static_cast<char>(client.read());
    if (ch == '\n') {
      commandBuffer[commandLength] = '\0';
      int left = 0;
      int right = 0;
      if (!discardCommand && parseCommand(commandBuffer, left, right)) {
        drive(left, right);
        lastCommandAt = millis();
        commandActive = true;
      } else {
        stopMotors();
        commandActive = false;
        Serial.println("Invalid command; stopped. Expected: LEFT RIGHT (-255..255).");
      }
      commandLength = 0;
      discardCommand = false;
    } else if (ch == '\0') {
      discardCommand = true;
    } else if (!discardCommand && commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = ch;
    } else {
      discardCommand = true;
    }
  }
  // A live TCP connection does not guarantee the GUI is still responding.
  if (commandActive && millis() - lastCommandAt >= COMMAND_TIMEOUT_MS) {
    stopMotors();
    commandActive = false;
    Serial.println("Command timeout; stopped. GUI must refresh commands.");
  }
}

