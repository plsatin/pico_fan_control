# Получение показаний температуры, скорости оборотов вентилятора и отправка данных в InfluxDB

from datetime import datetime
import time
import subprocess

from influxdb import InfluxDBClient



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
def send_to_influxdb(data):
    if not data:
        return

    # Формирование точки (point) для записи в базу
    json_body = [
        {
            "measurement": INFLUXDB_MEASUREMENT,
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

    try:
        influx_client.write_points(json_body)
        # print("Данные успешно записаны в InfluxDB")
    except Exception as e:
        print(f"Ошибка записи в InfluxDB: {e}")




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
            print(values) # Для отладки
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




def main():

    influx_client.create_database(INFLUXDB_DB)

    while True:
        time.sleep(5.0)
        data = fetch_fan_info()
        if data:
            # Запись в базу данных (локально)
            send_to_influxdb(data)      


# ---------------------------------------------
if __name__ == "__main__":
    main()
