# Получение показаний температуры и отображение графика

import time

import subprocess
import sys



# Пример данных: время и температура
# Время (например, часы)
time_list = []
counter = 0
# Температура (°C)
temperature = []
error_count = 0
# Вводные
temp_color = 'green'
temp_critical = 27.0


while True:
    counter = counter + 10
    temp_from_pico = 0.0
    temp_from_pico_prev = 0.0
    time_list.append(counter)

    try:
        temp_from_pico = subprocess.run([sys.executable, "fan_control.py", "temperature"], capture_output=True, text=True).stdout.strip()
        temp_from_pico = float(temp_from_pico)
        temperature.append(temp_from_pico)
        temp_from_pico_prev = temp_from_pico
    except Exception as exc:
        print(f"\n[!] Возникли ошибки: {exc}")
        temp_from_pico = temp_from_pico_prev
        temperature.append(temp_from_pico)
        error_count = error_count + 1

    print(f'Температура: {temp_from_pico}')

    if temp_from_pico > temp_critical:
        temp_color = 'red'
    else:
        temp_color = 'green'

    # Создание графика
    plt.plot(time_list, temperature, color=temp_color)

    # Настройка заголовка и меток осей
    plt.title('График температуры')
    plt.xlabel('Время, сек.')
    plt.ylabel('Температура, °C')

    plt.savefig("temperature_plot.png", dpi=180)

    # Держим график в диапозоне 600 секунд
    if counter > 600:
        # Удаляем устаревший элемент
        del time_list[0]
        del temperature[0]

    # После 10 ошибок 
    if error_count > 10:
        temp_from_pico = 0.0 
        temp_from_pico_prev = 0.0
        print(f'[!] Накопилось {error_count} ошибок')

    # Ждем 10 секунд перед сбором следующих данных
    time.sleep(10.0)
