# Получение показаний температуры, скорости оборотов вентилятора и отправка данных в InfluxDB

from datetime import datetime
import time
import subprocess

from influxdb import InfluxDBClient

import os
import json

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)



# --- Настройки InfluxDB ---
INFLUXDB_HOST = '192.168.0.209'  # Укажите ваш хост, если отличается
INFLUXDB_PORT = 8086
INFLUXDB_USER = 'icinga'      # Укажите вашего пользователя
INFLUXDB_PASSWORD = 'supersecret' # Укажите ваш пароль
INFLUXDB_DB = 'icinga2'
INFLUXDB_MEASUREMENT = 'smart-fan'

# Инициализация клиента InfluxDB
influx_client = InfluxDBClient(
    host=INFLUXDB_HOST,
    port=INFLUXDB_PORT,
    username=INFLUXDB_USER,
    password=INFLUXDB_PASSWORD,
    database=INFLUXDB_DB
)




# Функция отправки данных в InfluxDB
def send_to_influxdb(data, info_type='fan'):
    if not data:
        return

    if info_type == 'fan':
        # Формирование точки (point) для записи в базу
        json_body = [
            {
                "measurement": "smart-fan",
                "tags": {
                    "gpu": "GPU0" # Можно добавить динамическое определение, если карт несколько
                },
                "time": datetime.utcfromtimestamp(data["timestamp"] / 1000).isoformat() + "Z", # Время в UTC ISO8601
                "fields": {
                    "temperature": data["temperature"],
                    "fanSpeedPercentage": data["fanSpeedPercentage"],
                }
            }
        ]

    else:

        # Формирование точки (point) для записи в базу
        json_body = [
            {
                "measurement": "docker-01",
                "tags": {
                    "gpu": "GPU0" # Можно добавить динамическое определение, если карт несколько
                },
                "time": datetime.utcfromtimestamp(data["timestamp"] / 1000).isoformat() + "Z", # Время в UTC ISO8601
                "fields": {
                    "temperature": data["temperature"],
                    "gpuUtil": data["gpuUtil"],
                    "memUtil": data["memUtil"],
                    "power": data["power"],
                    "memUsed": data["memUsed"],
                    "memFree": data["memFree"]
                }
            }
        ]



    try:
        influx_client.write_points(json_body)
        # print("Данные успешно записаны в InfluxDB")
    except Exception as e:
        print(f"Ошибка записи в InfluxDB: {e}")






def fetch_gpu_info():
    cmd = 'nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,utilization.memory,power.draw,memory.used,memory.free --format=csv,noheader,nounits'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        values = result.stdout.strip().split(',')
        if len(values) < 4:
            return None

        data = {
            "timestamp": datetime.now().timestamp() * 1000,  # В миллисекундах для Chart.js
            "temperature": float(values[0]),
            "gpuUtil": float(values[1]),
            "memUtil": float(values[2]),
            "power": float(values[3]),
            "memUsed": float(values[4]),
            "memFree": float(values[5])

        }
        return data
    except subprocess.CalledProcessError as e:
        print(f"nvidia-smi error: {e}")
        return None



def fetch_fan_info():
    temp_from_pico = 0.0
    pwm_from_pico = 0
    fan_speed_percent = 0.0
    no_error_data = True

    cmd = 'python fan_control.py temperature,pwm'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        values = result.stdout.strip()

        try:
            # print(values) # Для отладки
            values_arr = values.split(";")
            temp_from_pico = float(values_arr[0])
            pwm_from_pico = int(values_arr[1])

            fan_speed_percent = float(pwm_from_pico / 65535 * 100)
            no_error_data = True
        except Exception as exc:
            print(f"\n[!] Возникли ошибки: {exc}")
            temp_from_pico = None
            fan_speed_percent = None
            no_error_data = False

        if no_error_data:
            data = {
                "timestamp": datetime.now().timestamp() * 1000,  # В миллисекундах для Chart.js
                "temperature": temp_from_pico,
                "fanSpeedPercentage": round(fan_speed_percent, 2)
            }

            return data
        else:
            return None

    except subprocess.CalledProcessError as e:
        print(f"App fan_control error: {e}")
        return None






# Маршрут для главной страницы
@app.route('/')
def index():
    return send_from_directory(os.path.abspath("."), "index.html")



# Фоновый процесс: опрос GPU и отправка данных
def background_task():
    while True:
        socketio.sleep(2)  # Ждём 2 секунды (аналог setInterval)
        data_gpu = fetch_gpu_info()

        if data_gpu:
            send_to_influxdb(data_gpu, 'gpu')      # Запись в базу данных (локально)
            socketio.emit('gpu_data', data_gpu)  # Трансляция клиентам


# Фоновый процесс: опрос GPU и отправка данных
def background_task2():
    while True:
        socketio.sleep(2)  # Ждём 2 секунды (аналог setInterval)
        data_fan = fetch_fan_info()

        if data_fan:
            send_to_influxdb(data_fan, 'fan')      # Запись в базу данных (локально)



# Запуск сервера
if __name__ == '__main__':
    # Убедитесь, что база данных существует (можно создать вручную через UI или запросом)
    influx_client.create_database(INFLUXDB_DB)

    socketio.start_background_task(background_task)
    socketio.start_background_task(background_task2)
    socketio.run(app, host='0.0.0.0', port=3000, debug=False)
