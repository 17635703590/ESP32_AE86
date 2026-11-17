/*
  ESP32-C3 小车：TB6612 双电机驱动 + 转向舵机 + 灯罩舵机

  目标开发板：ESP32C3 PRO MINI（或 ESP32-C3 SuperMini）
  构建工具：PlatformIO（board = esp32-c3-devkitc-02，framework = arduino）

  电机与舵机布局（两个 TT 驱动电机 + 两个 GH-S43D 舵机）：
    TB6612 A 通道 (PWMA/AIN1/AIN2) -> 驱动电机 1（AO1/AO2）：前进 / 后退
    TB6612 B 通道 (PWMB/BIN1/BIN2) -> 驱动电机 2（BO1/BO2）：前进 / 后退
    转向舵机（GPIO3，PWM 50Hz）   -> 前轮转向：左 / 右，超时后自动回中
    灯罩舵机（GPIO20，PWM 50Hz）  -> 车灯罩：随车灯联动，不回中

  F 前进：两个驱动电机同时正转（速度滑杆速度）
  B 后退：两个驱动电机同时反转（速度滑杆速度）
  L 左转：转向舵机转到左位，STEER_TIMEOUT_MS 内无新转向指令则自动回中
  R 右转：转向舵机转到右位，STEER_TIMEOUT_MS 内无新转向指令则自动回中
  S 停止：两路驱动立即停 + 转向舵机回中（灯罩舵机不受影响）
  H 车灯：前大灯 + 尾灯 + 第二路车灯(GPIO2)开/关切换，灯罩舵机联动（开灯=开罩、关灯=关罩，不回中）

  前进/后退速度由 0..9 档 / 速度滑杆控制；左/右转由转向舵机定位，不受速度滑杆影响。
  F/B 与 L/R 可同时生效（前进/后退的同时转向）。

  串口监听：该开发板只有原生 USB（USB-C），没有 USB 转串口芯片。必须在
  platformio.ini 中开启 -DARDUINO_USB_MODE=1 与 -DARDUINO_USB_CDC_ON_BOOT=1，
  让 Serial 走原生 USB CDC；否则 Serial 指向 UART0（GPIO20/21），USB-C 上
  收不到任何输出。原生 USB CDC 的波特率由主机协商，串口监视器设为 115200
  （或任意值）均可正常监听。

  接线：
  TB6612                    ESP32-C3
  PWMA, AIN1, AIN2          GPIO 4, 5, 6      // 驱动电机 1（AO1/AO2）
  PWMB, BIN1, BIN2          GPIO 7, 0, 1      // 驱动电机 2（BO1/BO2）
  STBY                      GPIO 10
  VCC                       3V3
  GND                       GND（必须与电机电源共地）
  VM                        电机电源（按 TT 电机额定电压，通常 3~6V）
  AO1/AO2                   驱动电机 1 两根线
  BO1/BO2                   驱动电机 2 两根线

  转向舵机（GH-S43D，3 线） ESP32-C3
  信号线（橙/黄）           GPIO 3（PWM 50Hz）
  VCC                       3.6~4.8V（如 5V 轨或 1S 锂电，切勿超压）
  GND                       GND（与主控共地）

  灯罩舵机（GH-S43D，3 线） ESP32-C3
  信号线（橙/黄）           GPIO 20（PWM 50Hz）
  VCC                       3.6~4.8V
  GND                       GND（与主控共地）

  车灯（LED，需串 ~220Ω 限流电阻）
  前大灯 + 尾灯 阳极        GPIO 21（两灯并联，同亮同灭）
  第二路车灯 阳极           GPIO 2（与 GPIO21 同亮同灭，注意 GPIO2 是启动 strap 脚）
  各灯 阴极                 GND（共地）

  注意：切勿用 3V3 给电机供电，电机电源只能走 VM（电池）。
  舵机工作电压 3.6~4.8V，用 3V3 供电偏低、可能无力甚至不动作，
  建议单独接 5V（USB 供电时）或 1S 锂电（3.7~4.2V）。
  ESP32-C3 的启动/strap 脚是 GPIO2、GPIO8、GPIO9。本项目把 GPIO2 用作第二路
  车灯控制（与 GPIO21 同亮同灭）：GPIO2 上电时内部上拉，串限流电阻驱动 LED 到
  GND 不影响正常启动；但切勿在 GPIO2 上接会把电平硬拉低的负载，否则复位/上电时
  可能进入下载模式。GPIO0/GPIO1/GPIO3 是普通 GPIO，接 BIN1/BIN2 与转向舵机
  不影响烧录与启动。
  GPIO20/GPIO21 原本是 UART0 的 RX/TX，本项目串口走原生 USB CDC，
  它们空闲，故 GPIO20 用作灯罩舵机信号、GPIO21 用作车灯控制脚。

  BLE 指令（通过 BLE UART/GATT 应用程序发送文本）：
  F = 前进（两个驱动电机正转）
  B = 后退（两个驱动电机反转）
  L = 左转（转向舵机左位，超时自动回中）
  R = 右转（转向舵机右位，超时自动回中）
  S = 停止（急停，驱动停 + 转向舵机回中）
  H = 车灯 开/关（切换，前大灯 + 尾灯 + 第二路车灯 + 灯罩舵机联动）
  0..9 = 设置前进/后退速度（0..255 分 10 级）

  安全保护：
    1) 转向舵机自动回中：L/R 设定角度后，若 STEER_TIMEOUT_MS 内没有新的
       L/R 指令，自动回到中位；需要立即回正可发送 S。
    2) 灯罩舵机不回中：随车灯状态保持开罩/关罩角度，不自动复位。
    3) 全局失联保护：运动中若 FAILSAFE_MS 内没有任何指令，自动停车（只停
       驱动，转向舵机由其自身超时回中）。把 FAILSAFE_MS 设为 0 可关闭。

  设备名称：C3-Car-TB6612
*/

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// Custom BLE UART-like service and writable command characteristic.
static const char *DEVICE_NAME = "C3-Car-TB6612";
static const char *SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char *COMMAND_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";

// A 通道：驱动电机 1（前进 / 后退） -> AO1/AO2
constexpr uint8_t PWMA = 4;
constexpr uint8_t AIN1 = 5;
constexpr uint8_t AIN2 = 6;
// B 通道：驱动电机 2（前进 / 后退） -> BO1/BO2
constexpr uint8_t PWMB = 7;
constexpr uint8_t BIN1 = 0;
constexpr uint8_t BIN2 = 1;
constexpr uint8_t STBY = 10;

// 转向舵机信号脚（前轮左右，超时自动回中）
constexpr uint8_t STEER_SERVO_PIN = 3;
// 灯罩舵机信号脚（随车灯联动，不回中）
constexpr uint8_t COVER_SERVO_PIN = 20;
// 车灯（前大灯 + 尾灯并联，LED 直驱，需串 ~220Ω 限流电阻）
constexpr uint8_t LIGHT_PIN = 21;
// 第二路车灯（与 LIGHT_PIN 同亮同灭）。注意：GPIO2 是启动 strap 脚，见文件头说明。
constexpr uint8_t LIGHT_PIN_2 = 2;

constexpr uint32_t SERVO_FREQ_HZ = 50;          // 标准舵机 PWM 频率
constexpr uint8_t SERVO_RESOLUTION_BITS = 14;   // ESP32-C3 LEDC 最高 14 位

// 转向舵机脉宽（微秒）。典型 1000~2000us，中位 1500us。
// 若左/右方向相反，交换下面两个值即可；
// 若转向角度过大顶到机械限位，可向中间收窄（如 1200/1800）。
constexpr uint16_t STEER_CENTER_US = 1500;
constexpr uint16_t STEER_LEFT_US = 1000;
constexpr uint16_t STEER_RIGHT_US = 2000;

// 灯罩舵机脉宽（微秒）：开灯=开罩、关灯=关罩，方向反了交换两者即可。
// 按 1000~2000µs = 0~180°（约 5.56µs/°）换算，开罩/关罩各 +10° ≈ +56µs。
constexpr uint16_t COVER_OPEN_US = 2056;    // 约 190°
constexpr uint16_t COVER_CLOSED_US = 1056;  // 约 10°

constexpr unsigned long STEER_TIMEOUT_MS = 600; // 转向舵机自动回中时间
constexpr unsigned long FAILSAFE_MS = 1500;      // 全局失联保护，0 = 关闭
constexpr unsigned long STATUS_INTERVAL_MS = 3000;

uint8_t driveSpeed = 0;   // 前进/后退速度 0..255，默认 0：上电即停止
char driveState = 'S';    // 驱动状态：F / B / S
char steerState = 'S';    // 转向状态：L / R / S（S = 回中，超时自动回中）

bool lightOn = false;     // 车灯（前大灯 + 尾灯）+ 灯罩舵机联动

unsigned long lastCommandMs = 0;
unsigned long lastSteerMs = 0;   // 最后一次转向指令时间，用于自动回中

bool bleConnected = false;
BLEServer *bleServer = nullptr;
BLEAdvertising *bleAdvertising = nullptr;
unsigned long lastSerialStatusMs = 0;

// 舵机脉宽（微秒）转 LEDC 占空比（14 位 @ 50Hz，周期 20000us）
uint32_t servoDuty(uint16_t pulseUs) {
  pulseUs = constrain(pulseUs, 500, 2500);  // 夹到舵机安全范围
  return (uint32_t)pulseUs * ((1UL << SERVO_RESOLUTION_BITS) - 1) /
         (1000000UL / SERVO_FREQ_HZ);
}

void servoWrite(uint8_t pin, uint16_t pulseUs) {
  ledcWrite(pin, servoDuty(pulseUs));
}

uint16_t currentSteerUs() {
  if (steerState == 'L') return STEER_LEFT_US;
  if (steerState == 'R') return STEER_RIGHT_US;
  return STEER_CENTER_US;
}

uint16_t currentCoverUs() {
  return lightOn ? COVER_OPEN_US : COVER_CLOSED_US;
}

void applyLights() {
  digitalWrite(LIGHT_PIN, lightOn ? HIGH : LOW);
  digitalWrite(LIGHT_PIN_2, lightOn ? HIGH : LOW);  // 第二路车灯与主车灯同亮同灭
  servoWrite(COVER_SERVO_PIN, currentCoverUs());  // 灯罩舵机随灯联动
}

void lightsOff() {
  lightOn = false;
  applyLights();
}

void setMotor(uint8_t pwmPin, uint8_t in1, uint8_t in2, int speed) {
  speed = constrain(speed, -255, 255);
  if (speed > 0) {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
    analogWrite(pwmPin, speed);
  } else if (speed < 0) {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
    analogWrite(pwmPin, -speed);
  } else {
    analogWrite(pwmPin, 0);
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);
  }
}

void applyOutputs() {
  // 两个驱动电机：同向驱动（前进 / 后退）
  int drive = 0;
  if (driveState == 'F') {
    drive = driveSpeed;
  } else if (driveState == 'B') {
    drive = -driveSpeed;
  }
  setMotor(PWMA, AIN1, AIN2, drive);
  setMotor(PWMB, BIN1, BIN2, drive);

  // 转向舵机：定位（L/R，超时自动回中）
  servoWrite(STEER_SERVO_PIN, currentSteerUs());

  // 驱动停止时关断 TB6612，省电
  digitalWrite(STBY, (driveState != 'S') ? HIGH : LOW);
}

void stopDrive() {
  driveState = 'S';
  applyOutputs();  // 只停驱动，转向舵机保持（由其自身超时回中）
}

void stopAll() {
  driveState = 'S';
  steerState = 'S';
  applyOutputs();  // 停车 + 转向舵机回中
}

// 打印当前状态
void logState() {
  Serial.print("[STATE] drive=");
  Serial.print(driveState);
  Serial.print("/");
  Serial.print(driveSpeed);
  Serial.print(" steer=");
  Serial.print(steerState);
  Serial.print("/");
  Serial.print(currentSteerUs());
  Serial.print("us cover=");
  Serial.print(currentCoverUs());
  Serial.print("us light=");
  Serial.print(lightOn ? "ON" : "OFF");
  Serial.println();
}

void handleCommand(String command) {
  command.trim();
  command.toUpperCase();
  if (command.length() == 0) return;

  const char c = command.charAt(0);
  lastCommandMs = millis();

  if (c >= '0' && c <= '9') {
    driveSpeed = static_cast<uint8_t>((c - '0') * 255 / 9);
    applyOutputs();  // 行驶中调速度立即生效
    logState();
    return;
  }

  switch (c) {
    case 'F': driveState = 'F'; break;
    case 'B': driveState = 'B'; break;
    case 'L':
      steerState = 'L';
      lastSteerMs = millis();   // 超时后自动回中
      break;
    case 'R':
      steerState = 'R';
      lastSteerMs = millis();   // 超时后自动回中
      break;
    case 'S':
      driveState = 'S';
      steerState = 'S';   // 停车 + 转向舵机回中
      break;
    case 'H':
      lightOn = !lightOn;   // 车灯 + 灯罩舵机联动
      break;
    default:
      // 未知指令按停止处理，保证安全
      driveState = 'S';
      steerState = 'S';
      break;
  }
  applyOutputs();
  applyLights();
  logState();
}

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    bleConnected = true;
    Serial.println("[BLE] Client connected");
    stopAll();   // 连接即停 + 转向舵机回中，避免上电前残留状态
    lightsOff();  // 灯归零（关灯 + 关罩），与上位机状态保持一致
  }

  void onDisconnect(BLEServer *) override {
    bleConnected = false;
    Serial.println("[BLE] Client disconnected; advertising restarted");
    stopAll();
    lightsOff();
    if (bleAdvertising != nullptr) bleAdvertising->start();
  }
};

class CommandCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String value = characteristic->getValue();
    if (value.length() > 0) {
      Serial.print("[BLE RX] ");
      Serial.println(value);
      handleCommand(value);
    }
  }
};

void setup() {
  Serial.begin(115200);
  // 原生 USB CDC 在主机打开串口前 !Serial 为真；最多等待 5 秒，
  // 既能在监视器已打开时收到启动信息，又不会阻塞 BLE 启动。
  const unsigned long serialWaitStart = millis();
  while (!Serial && millis() - serialWaitStart < 5000) {
    delay(10);
  }
  Serial.println();
  Serial.println("ESP32-C3 TB6612 drive + steer/cover servo controller starting...");

  pinMode(PWMA, OUTPUT);
  pinMode(AIN1, OUTPUT);
  pinMode(AIN2, OUTPUT);
  pinMode(PWMB, OUTPUT);
  pinMode(BIN1, OUTPUT);
  pinMode(BIN2, OUTPUT);
  pinMode(STBY, OUTPUT);

  pinMode(LIGHT_PIN, OUTPUT);
  pinMode(LIGHT_PIN_2, OUTPUT);

  // 两个舵机 PWM：50Hz、14 位，直接挂到 GPIO
  ledcAttach(STEER_SERVO_PIN, SERVO_FREQ_HZ, SERVO_RESOLUTION_BITS);
  ledcAttach(COVER_SERVO_PIN, SERVO_FREQ_HZ, SERVO_RESOLUTION_BITS);
  stopAll();    // 电机停 + 转向舵机回中
  lightsOff();  // 灯默认关闭（关罩）

  BLEDevice::init(DEVICE_NAME);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallbacks());

  BLEService *service = bleServer->createService(SERVICE_UUID);
  BLECharacteristic *commandCharacteristic = service->createCharacteristic(
      COMMAND_UUID,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  commandCharacteristic->setCallbacks(new CommandCallbacks());
  service->start();

  bleAdvertising = BLEDevice::getAdvertising();
  bleAdvertising->addServiceUUID(SERVICE_UUID);
  bleAdvertising->setScanResponse(true);
  bleAdvertising->start();
  Serial.print("[BLE] Advertising as: ");
  Serial.println(DEVICE_NAME);
  Serial.println("[BLE] Waiting for a client connection");
}

void loop() {
  const unsigned long now = millis();

  // 转向舵机自动回中：L/R 后 STEER_TIMEOUT_MS 内无新的转向指令则回中
  if (steerState != 'S' && (now - lastSteerMs) > STEER_TIMEOUT_MS) {
    steerState = 'S';
    applyOutputs();
    Serial.println("[STEER] auto-center");
  }

  // 全局失联保护：运动中长时间无任何指令则停车（转向舵机由其自身超时回中）
  if (FAILSAFE_MS > 0 && driveState != 'S' &&
      (now - lastCommandMs) > FAILSAFE_MS) {
    stopDrive();
    Serial.println("[FAILSAFE] no command; motors stopped");
  }

  // 周期性状态，便于串口监听
  if (now - lastSerialStatusMs >= STATUS_INTERVAL_MS) {
    lastSerialStatusMs = now;
    Serial.print("[STATUS] BLE=");
    Serial.print(bleConnected ? "connected" : "advertising");
    Serial.print(" drive=");
    Serial.print(driveState);
    Serial.print("/");
    Serial.print(driveSpeed);
    Serial.print(" steer=");
    Serial.print(steerState);
    Serial.print("/");
    Serial.print(currentSteerUs());
    Serial.print("us cover=");
    Serial.print(currentCoverUs());
    Serial.print("us light=");
    Serial.print(lightOn ? "ON" : "OFF");
    Serial.println();
  }
  delay(20);
}
