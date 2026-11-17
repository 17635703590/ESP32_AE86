# ESP32 AE86

基于 **ESP32-C3** 的 BLE 遥控小车项目。两个 TT 直流电机负责前进和后退，转向舵机控制前轮，另一只舵机联动车灯罩；电脑端提供一个 Python 桌面控制界面。

## 功能

- 通过 BLE 控制小车，设备名称为 `C3-Car-TB6612`。
- TB6612FNG 驱动双电机，支持前进、后退和 10 档速度。
- 转向舵机支持左转、右转和自动回中。
- 前灯、尾灯及第二路车灯同步开关，灯罩舵机随车灯开合。
- 带运动失联停车保护；BLE 连接或断开时会停车、回正并关闭车灯。
- 提供基于 Tkinter 和 Bleak 的电脑端 BLE 控制程序。

## 硬件

- ESP32-C3 开发板（如 ESP32-C3 SuperMini / PRO MINI）
- TB6612FNG 双路电机驱动模块
- TT 减速电机 × 2
- GH-S43D 微型舵机 × 2（转向、灯罩）
- LED 车灯及限流电阻
- 电机电源、舵机电源和连接线

### 引脚速查

| 功能 | ESP32-C3 引脚 | 连接说明 |
| --- | --- | --- |
| TB6612 PWMA / AIN1 / AIN2 | GPIO4 / GPIO5 / GPIO6 | A 通道，驱动电机 1 |
| TB6612 PWMB / BIN1 / BIN2 | GPIO7 / GPIO0 / GPIO1 | B 通道，驱动电机 2 |
| TB6612 STBY | GPIO10 | 驱动使能 |
| 转向舵机信号 | GPIO3 | PWM 50 Hz |
| 灯罩舵机信号 | GPIO20 | PWM 50 Hz |
| 前灯与尾灯 | GPIO21 | 并联控制，各 LED 分别串限流电阻 |
| 第二路车灯 | GPIO2 | 与主车灯同步；注意这是启动配置脚 |

完整接线、电源和校准说明见 [`接线说明书.md`](接线说明书.md)。

> **电源注意：** TB6612 的 VCC 接 3.3 V，VM 接符合电机额定电压的电源；电机和舵机不要由开发板 3.3 V 引脚供电。舵机使用符合其规格的 3.6–4.8 V 电源。ESP32-C3、驱动板和舵机必须共地。GPIO2 上不要连接会在上电时将其强拉低的负载，以免影响启动。

## BLE 命令

设备广播名为 `C3-Car-TB6612`。服务 UUID 为 `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`，命令写入特征 UUID 为 `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`。向命令特征发送单个 ASCII 字符：

| 命令 | 功能 |
| --- | --- |
| `F` | 前进 |
| `B` | 后退 |
| `L` / `R` | 左转 / 右转；转向舵机约 600 ms 后自动回中 |
| `S` | 停止驱动电机并让转向舵机回中 |
| `H` | 切换车灯；灯罩舵机同步开合 |
| `0`–`9` | 设置前进/后退速度，`0` 最低，`9` 最高 |

前进和后退会持续到收到停止命令。运动中若 1.5 秒没有收到任何命令，固件会自动停止驱动电机。上电默认速度为 0，行驶前先设置速度。

## 编译与烧录

项目使用 PlatformIO 和 Arduino 框架。安装 PlatformIO 后，在项目根目录运行：

```bash
pio run -e featheresp32
pio run -e featheresp32 -t upload
```

串口日志使用 ESP32-C3 原生 USB CDC。`platformio.ini` 已启用 USB CDC，串口监视器波特率设为 `115200`：

```bash
pio device monitor -e featheresp32
```

## 电脑端控制界面

需要安装 Python 3、可用的 Bluetooth LE 适配器，以及项目列出的 Python 依赖：

```bash
python -m pip install -r tools/requirements.txt
python tools/car_gui.py
```

在界面中扫描并连接 `C3-Car-TB6612`。方向键或 `W/A/S/D` 可控制行驶和转向；按住前进/后退可持续行驶，松开即停车。空格、`Esc` 或界面中的急停按钮会发送 `S`。速度滑杆用于选择 0–9 档，车灯按钮用于发送 `H`。

## 目录结构

```text
src/main.cpp              ESP32-C3 固件
platformio.ini            PlatformIO 构建配置
tools/car_gui.py          Python BLE 桌面控制界面
tools/requirements.txt    Python 依赖
接线说明书.md             详细接线、供电与调试说明
```

## 安全提示

- 首次调试时先架空车轮，确认电机方向、舵机行程和急停正常后再落地。
- 确认舵机不会顶住机械限位；如有异响或发热，应调整舵机脉宽或机械结构。
- LED 应串联限流电阻；较大电流负载请使用合适的三极管或 MOSFET 驱动。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。
