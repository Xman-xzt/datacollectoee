import sys
import minimalmodbus
import serial
import json
import socket
import os
import time
from datetime import datetime
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import QTimer
from 多线程UI import Ui_Form  # 假设这个模块是通过PyQt Designer生成的

class ScannerThread(QtCore.QThread):
    # 自定义信号，用于发送扫码头接收到的数据
    data_received = QtCore.pyqtSignal(str)

    def __init__(self, ip='192.168.0.22', port=9102):
        super().__init__()
        self.ip = ip
        self.port = port
        self.socket = None
        self.is_running = True

    def run(self):
        """连接到扫码头并持续接收数据"""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((self.ip, self.port))
            print("扫码头连接成功")
        except Exception as e:
            print(f"扫码头连接失败: {e}")
            return

        while self.is_running:
            try:
                data = self.socket.recv(4096).decode('utf-8').strip()  # 读取并解码数据
                if data:
                    self.data_received.emit(data)  # 发送接收到的数据
                    # self.stop()
                    # break
            except Exception as e:
                print(f"扫码头数据读取失败: {e}")
                break

    def stop(self):
        """停止线程并关闭与扫码头的连接"""
        self.is_running = False
        if self.socket:
            self.socket.close()

class MainWindow(QtWidgets.QWidget, Ui_Form):
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)  # 初始化UI界面
        self.init_ui()      # 初始化UI组件和信号槽
        self.init_devices() # 初始化设备连接

    def init_ui(self):
        """初始化UI组件和信号槽连接"""
        # 按钮点击事件连接
        self.pushButton.clicked.connect(self.start_reading)   # 开始读取按钮
        self.pushButton_2.clicked.connect(self.stop_reading)  # 停止读取按钮

        # 初始化定时器
        self.init_timers()

        # 初始化状态变量
        self.file_counter = 1        # 文件计数器，用于生成唯一文件名
        self.last_m280_state = 0     # 上一次的M280线圈状态
        self.is_writing = False      # 是否正在写入数据
        self.start_time = None       # 计时器开始时间

    def init_timers(self):
        """初始化各个定时器"""
        # 定时器用于更新压力和温度数据
        self.data_timer = QTimer(self)
        self.data_timer.timeout.connect(self.update_pressure_and_temperature)

        # 定时器用于监控M280线圈状态
        self.m280_timer = QTimer(self)
        self.m280_timer.timeout.connect(self.monitor_m280_coil)

        # 定时器用于监控M270线圈状态
        self.m270_timer = QTimer(self)
        self.m270_timer.timeout.connect(self.monitor_m270_coil)

        # 定时器用于更新计时器显示
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self.update_elapsed_time)

    def init_devices(self):
        """初始化设备连接和通信"""
        # 初始化Modbus设备
        self.instrument_pressure = self.configure_instrument('COM10', 4)  # 压力传感器
        self.instrument_pressure2 = self.configure_instrument('COM10', 5)  # 压力传感器
        self.instrument_temperature = self.configure_instrument('COM10', 3)  # 温度传感器
        self.instrument_coil = self.configure_instrument('COM10', 2)  # PLC线圈

        # 初始化扫码头线程
        self.scanner_thread = None
        self.scanner_data = ""  # 存储扫码头读取的数据

    def configure_instrument(self, port, slave_address):
        """配置Modbus RTU通信的仪表设备"""
        instrument = minimalmodbus.Instrument(port, slave_address)
        instrument.serial.baudrate = 9600
        instrument.serial.bytesize = 8
        instrument.serial.parity = serial.PARITY_EVEN
        instrument.serial.stopbits = 1
        instrument.serial.timeout = 1
        instrument.mode = minimalmodbus.MODE_RTU
        return instrument

    def monitor_m280_coil(self):
        """监控M280线圈状态变化，控制数据写入流程"""
        coil_state = self.read_plc_coil(2328)  # 读取M280线圈状态
        if coil_state is None:
            return  # 如果读取失败，直接返回

        # 检测线圈状态变化
        if coil_state == 1 and self.last_m280_state == 0:
            self.is_writing = True  # 开始写入数据
            print("开始数据写入")
        elif coil_state == 0 and self.last_m280_state == 1:
            self.is_writing = False  # 停止写入数据
            self.write_to_file()     # 保存数据到文件
            print("停止数据写入并保存文件")

        self.last_m280_state = coil_state  # 更新上一次线圈状态

    def monitor_m270_coil(self):
        """监控M270线圈状态变化，控制计时器启动和停止"""
        coil_state = self.read_plc_coil(2318)  # 读取M270线圈状态
        if coil_state is None:
            return  # 如果读取失败，直接返回

        # 检测线圈状态变化，控制计时器
        if coil_state == 1 and self.start_time is None:
            self.start_time = time.time()  # 记录开始时间
            self.lcdNumber_3.display(0)    # 重置显示
            self.elapsed_timer.start(100)  # 开始计时，每100毫秒更新一次
            print("计时开始")
        elif coil_state == 0 and self.start_time is not None:
            self.elapsed_timer.stop()  # 停止计时
            elapsed = round(time.time() - self.start_time, 2)  # 计算经过时间
            self.lcdNumber_3.display(elapsed)  # 显示最终时间
            self.start_time = None  # 重置开始时间
            print(f"计时停止，持续时间：{elapsed} 秒")

    def read_plc_coil(self, coil_address):
        """读取指定PLC线圈的状态"""
        try:
            return self.instrument_coil.read_bit(coil_address, functioncode=1)
        except Exception as e:
            print(f"读取PLC线圈失败: {e}")
            return None

    def start_reading(self):
        """开始读取数据和监控"""
        # 启动定时器
        self.data_timer.start(1500)   # 每秒读取一次压力和温度
        self.m280_timer.start(1000)   # 每秒监控一次M280线圈
        self.m270_timer.start(1000)    # 每0.5秒监控一次M270线圈

        # 启动扫码头线程
        if self.scanner_thread is None:
            self.scanner_thread = ScannerThread()
            self.scanner_thread.data_received.connect(self.update_scanner_data)
            self.scanner_thread.start()
            print("扫码头线程启动")

    def stop_reading(self):
        """停止读取数据和监控"""
        # 停止所有定时器
        self.data_timer.stop()
        self.m280_timer.stop()
        self.m270_timer.stop()
        self.elapsed_timer.stop()

        # 停止扫码头线程
        if self.scanner_thread:
            self.scanner_thread.stop()
            self.scanner_thread = None
            print("扫码头线程停止")

        # 重置显示
        self.lcdNumber.display('0')
        self.lcdNumber_2.display('0')
        self.lcdNumber_3.display('0')
        self.lcdNumber_4.display('0')
        self.lcdNumber_5.display('0')

        # 保存数据到文件（如果有需要）
        if self.is_writing:
            self.write_to_file()
            self.is_writing = False

    def update_pressure_and_temperature(self):
        """更新压力和温度数据，并显示在界面上"""
        try:
            raw_pressure = self.instrument_pressure.read_register(1, 0)
            self.current_pressure = (raw_pressure - 65535) / 1000 if raw_pressure > 10000 else raw_pressure / 1000
            self.lcdNumber.display(self.current_pressure)
            print(f"当前压力：{self.current_pressure} MPa")
        except Exception as e:
            print(f"压力读取失败: {e}")
            self.reconnect_instrument(self.instrument_pressure)  # 尝试重新连接

        try:
            raw_pressure2 = self.instrument_pressure2.read_register(1, 0)
            self.current_pressure2 = round((raw_pressure2 * 3.14 * 625 * 0.8) / 1000,2)
            self.lcdNumber_5.display(self.current_pressure2)
            print(f"当前压力：{self.current_pressure2} MPa")
        except Exception as e:
            print(f"压力读取失败: {e}")
            self.reconnect_instrument(self.instrument_pressure2)  # 尝试重新连接

        try:
            self.current_temperature = self.instrument_temperature.read_register(8192, 0)
            self.lcdNumber_2.display(self.current_temperature)
            print(f"当前温度：{self.current_temperature} ℃")
        except Exception as e:
            print(f"温度读取失败: {e}")
            self.reconnect_instrument(self.instrument_temperature)  # 尝试重新连接

    def reconnect_instrument(self, instrument):
        """重新连接设备"""
        try:
            instrument.serial.close()  # 先关闭连接
            instrument.serial.open()  # 然后重新打开连接
            print(f"重新连接 {instrument} 成功")
        except Exception as e:
            print(f"重新连接失败: {e}")

    def update_elapsed_time(self):
        """更新计时器显示"""
        if self.start_time:
            elapsed = round(time.time() - self.start_time, 2)
            self.lcdNumber_3.display(elapsed)

    def update_scanner_data(self, data):
        """更新扫码头数据并显示"""
        self.scanner_data = data
        self.lcdNumber_4.display(data)
        print(f"扫码头数据：{data}")

    def get_next_file_path(self):
        """获取下一个可用的文件路径"""
        directory = r'C:\Users\admin\Desktop\jsondata'
        os.makedirs(directory, exist_ok=True)
        while True:
            file_path = os.path.join(directory, f'{self.file_counter:03}.json')
            if not os.path.exists(file_path):
                return file_path
            self.file_counter += 1

    def write_to_file(self):
        """将当前数据写入JSON文件"""
        data_collect = {
            "pcIp": "172.16.192.70",
            "equipmentIp": "PB press",
            "equipmentNode": "press",
            "collectData": f'{self.scanner_data};{self.current_pressure};{self.current_pressure2};{self.current_temperature};{self.lcdNumber_3.value()}',
            "dataType":"A",
            "collectTime": datetime.now().isoformat(timespec='seconds')
        }

        file_path = self.get_next_file_path()
        try:
            with open(file_path, 'w') as f:
                json.dump(data_collect, f, separators=(',', ':'), ensure_ascii=False)

            print(f"数据写入成功: {file_path}")
        except Exception as e:
            print(f"文件写入失败: {e}")

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    mainWin = MainWindow()
    mainWin.show()
    sys.exit(app.exec_())