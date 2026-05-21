import serial

ser = serial.Serial("COM5", baudrate=115200, timeout=5)


import matplotlib.pyplot as plt
# from datetime import datetime
import time


# Пример данных: время и температура
# Время (например, часы)
time_list = []
counter = 0
# Температура (°C)
temperature = []


start_text = ser.readline().decode('utf-8').strip()

while True:

    counter = counter + 1
    time_list.append(counter)
    temp_from_pico = 0.0

    ## Читаем строку из порта и отрезаем лишнее
    temp_from_pico = ser.readline().decode('utf-8').strip()
    try:
        temperature.append(float(temp_from_pico))
    except Exception as exc:
        print(f"\n[!] Возникли ошибки: {exc}")
        temperature.append(0.0)

    print(f'Температура: {temp_from_pico}')
    # print(temp_from_pico)

    # Создание графика
    plt.plot(time_list, temperature)

    # Настройка заголовка и меток осей
    plt.title('График температуры')
    plt.xlabel('Время (5 сек.)')
    plt.ylabel('Температура (°C)')

    # Отображение графика
    # plt.show()

    ## plt.show - блокирует выполнение программы поэтому используем pause - показывая график на 5 сек.
    plt.pause(5)
    plt.close()

    # time.sleep(5.0)

    # Сбрасываем счетчики
    if counter > 120:
        time_list = []
        counter = 0
        temperature = []
